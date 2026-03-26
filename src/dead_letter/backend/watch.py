"""Watch manager for auto-creating jobs from stable `.eml` files."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Protocol

from watchfiles import Change, DefaultFilter, awatch

from dead_letter.backend.filesystem import FilesystemBrowser
from dead_letter.backend.schemas import ErrorItem
from dead_letter.backend.schemas import JobCreateRequest, JobOptions


class _JobCreator(Protocol):
    async def create_job(self, request: JobCreateRequest, *, origin: str = "manual") -> object: ...


class _EmlFilter(DefaultFilter):
    def __call__(self, change: Change, path: str) -> bool:
        if not super().__call__(change, path):
            return False
        if "/_batch-" in path.replace("\\", "/"):
            return False
        return path.lower().endswith(".eml")


@dataclass(slots=True)
class WatchStatus:
    active: bool = False
    path: str | None = None
    files_detected: int = 0
    jobs_created: int = 0
    failed_events: int = 0
    last_error: ErrorItem | None = None
    latest_job_id: str | None = None


class WatchManager:
    """Watch one directory at a time for existing or new stable `.eml` files."""

    def __init__(
        self,
        *,
        browser: FilesystemBrowser,
        dedupe_window_seconds: float = 5.0,
        stable_poll_interval: float = 0.2,
        stable_observations_required: int = 2,
        stable_timeout_seconds: float = 5.0,
    ) -> None:
        self._browser = browser
        self._dedupe_window_seconds = dedupe_window_seconds
        self._stable_poll_interval = stable_poll_interval
        self._stable_observations_required = stable_observations_required
        self._stable_timeout_seconds = stable_timeout_seconds
        self._task: asyncio.Task[None] | None = None
        self._stop_event: asyncio.Event | None = None
        self._status = WatchStatus()
        self._recent_paths: dict[Path, float] = {}

    def status(self) -> WatchStatus:
        return WatchStatus(
            active=self._status.active,
            path=self._status.path,
            files_detected=self._status.files_detected,
            jobs_created=self._status.jobs_created,
            failed_events=self._status.failed_events,
            last_error=self._status.last_error.model_copy(deep=True) if self._status.last_error else None,
            latest_job_id=self._status.latest_job_id,
        )

    def suppress_path(self, path: str | Path) -> None:
        self._remember_recent_path(Path(path).expanduser().resolve())

    async def start(self, path: str, options: dict[str, object], job_manager: _JobCreator) -> None:
        if self._status.active:
            raise RuntimeError("watch session already active")

        raw_path = Path(path).expanduser()
        if raw_path.is_absolute():
            resolved = raw_path.resolve()
        else:
            normalized_path = self._browser.normalize_relative(path)
            resolved = self._browser.resolve_relative(normalized_path)
        if not resolved.exists():
            raise FileNotFoundError(f"path does not exist: {path}")
        if not resolved.is_dir():
            raise ValueError(f"path is not a directory: {path}")

        startup_paths = self._snapshot_existing_eml_files(resolved)
        self._recent_paths.clear()
        self._stop_event = asyncio.Event()
        self._status = WatchStatus(active=True, path=str(resolved))
        self._task = asyncio.create_task(self._watch_loop(resolved, startup_paths, options, job_manager))

    async def stop(self) -> None:
        task = self._task
        stop_event = self._stop_event
        if task is None and stop_event is None:
            return

        if stop_event is not None:
            stop_event.set()
        if task is not None:
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except Exception as exc:  # pragma: no cover - defensive shutdown guard
                self._record_failure(None, exc)

        self._status.active = False
        self._task = None
        self._stop_event = None

    async def _watch_loop(
        self,
        directory: Path,
        startup_paths: list[Path],
        options: dict[str, object],
        job_manager: _JobCreator,
    ) -> None:
        if self._stop_event is None:
            raise RuntimeError("watch stop event is not initialized")
        stop_event = self._stop_event

        try:
            for candidate in startup_paths:
                if stop_event.is_set():
                    return
                try:
                    await self._process_candidate(candidate, options, job_manager)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._record_failure(candidate, exc)

            async for changes in awatch(
                directory,
                watch_filter=_EmlFilter(),
                stop_event=stop_event,
                debounce=300,
                step=75,
            ):
                for change_type, changed_path in sorted(changes, key=lambda item: item[1]):
                    if change_type not in {Change.added, Change.modified}:
                        continue

                    candidate = Path(changed_path).resolve()
                    try:
                        await self._process_candidate(candidate, options, job_manager)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self._record_failure(candidate, exc)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - defensive watcher guard
            self._record_failure(None, exc)
        finally:
            self._status.active = False

    async def _wait_for_stable_file(self, path: Path) -> Path | None:
        deadline = monotonic() + self._stable_timeout_seconds
        previous_signature: tuple[int, int] | None = None
        stable_observations = 0

        while monotonic() < deadline:
            if self._stop_event is not None and self._stop_event.is_set():
                return None
            if not path.exists() or not path.is_file():
                await asyncio.sleep(self._stable_poll_interval)
                continue

            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns)
            if signature == previous_signature:
                stable_observations += 1
                if stable_observations >= self._stable_observations_required:
                    return path.resolve()
            else:
                previous_signature = signature
                stable_observations = 1

            await asyncio.sleep(self._stable_poll_interval)

        return None

    async def _process_candidate(
        self,
        candidate: Path,
        options: dict[str, object],
        job_manager: _JobCreator,
    ) -> None:
        if self._is_recent_duplicate(candidate):
            return

        stable_path = await self._wait_for_stable_file(candidate)
        if stable_path is None:
            return
        if not self._is_within_active_watch_root(stable_path):
            return
        if self._is_recent_duplicate(stable_path):
            return

        self._remember_recent_path(stable_path)
        self._status.files_detected += 1
        request = JobCreateRequest(
            mode="file",
            input_path=str(stable_path),
            options=JobOptions(**options),
        )
        created_job = await job_manager.create_job(request, origin="watch")
        self._status.jobs_created += 1
        latest_job_id = getattr(created_job, "id", None)
        if isinstance(latest_job_id, str) and latest_job_id:
            self._status.latest_job_id = latest_job_id

    def _snapshot_existing_eml_files(self, directory: Path) -> list[Path]:
        dir_filter = DefaultFilter()
        file_filter = _EmlFilter()
        startup_paths: list[Path] = []

        for root, dirnames, filenames in os.walk(directory, topdown=True):
            root_path = Path(root)
            dirnames[:] = sorted(
                name
                for name in dirnames
                if dir_filter(Change.added, str(root_path / name)) and not name.startswith("_batch-")
            )
            for name in sorted(filenames):
                candidate = root_path / name
                if not file_filter(Change.added, str(candidate)):
                    continue
                startup_paths.append(candidate.resolve())

        return startup_paths

    def _is_recent_duplicate(self, path: Path) -> bool:
        now = monotonic()
        self._recent_paths = {
            candidate: seen_at
            for candidate, seen_at in self._recent_paths.items()
            if now - seen_at < self._dedupe_window_seconds
        }
        seen_at = self._recent_paths.get(path)
        return seen_at is not None and now - seen_at < self._dedupe_window_seconds

    def _remember_recent_path(self, path: Path) -> None:
        self._recent_paths[path] = monotonic()

    def _is_within_active_watch_root(self, path: Path) -> bool:
        if self._status.path is None:
            return False
        watch_root = Path(self._status.path).resolve()
        return path.is_relative_to(watch_root)

    def _record_failure(self, path: Path | None, exc: Exception) -> None:
        self._status.failed_events += 1
        self._status.last_error = ErrorItem(
            path=str(path.resolve()) if path is not None else None,
            code="watch_processing_error",
            message=str(exc) or exc.__class__.__name__,
            stage="backend",
        )
