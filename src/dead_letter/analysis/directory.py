"""Bounded directory execution; sidecars alone decide validated reuse."""

from __future__ import annotations

import asyncio
import math
import os
from pathlib import Path
from typing import Callable

from dead_letter.analysis.contracts import DEFAULT_MODEL, AnalysisError
from dead_letter.analysis.providers.typesafe import (
    TypeSafeConfig, TypeSafeProvider, emit_disclosure, preflight,
)
from dead_letter.analysis.sidecar import analyze_to_sidecar
from dead_letter.core._pipeline import _iter_source_eml_files

_AUTH = {"provider_authentication_failed", "provider_permission_denied"}
_REJECTED = {"provider_validation_failed", "provider_bad_request"}
_THROTTLED = {
    "provider_rate_limited", "provider_unavailable", "provider_timeout",
    "provider_budget_exceeded", "provider_connection_failed",
}


class DirectoryAnalysisInterrupted(asyncio.CancelledError):
    """External cancellation, with the completed run's partial JSON summary."""

    def __init__(self, summary: dict):
        super().__init__()
        self.summary = summary


async def analyze_directory(
    source_dir: str | Path, output_dir: str | Path, *, provider: str,
    allow_remote: bool = False, jobs: int = 4,
    profile_name: str = "triage-v1", focus_identity: tuple[str, ...] = (),
    max_context_segments: int = 3, model: str = DEFAULT_MODEL,
    config: TypeSafeConfig | None = None, alias_max_age: float = 86400,
    on_disclosure: Callable[[dict], None] = emit_disclosure,
) -> dict:
    """Analyze each discovered EML independently and return a version 1 summary.

    Output must be outside the source tree. Reuse needs no credentials; fresh
    work shares one lazy preflight. Cancellation propagates as
    DirectoryAnalysisInterrupted with a partial summary after workers close.
    """
    if allow_remote is not True:
        raise AnalysisError("remote_analysis_not_authorized")
    if provider != "typesafe":
        raise AnalysisError("unsupported_analysis_provider")
    if type(jobs) is not int or not 1 <= jobs <= 16:
        raise AnalysisError("invalid_analysis_jobs")
    if type(alias_max_age) not in (int, float) or not math.isfinite(alias_max_age) or alias_max_age < 0:
        raise AnalysisError("invalid_alias_max_age")
    try:
        source = Path(source_dir).expanduser().resolve()
        output = Path(output_dir).expanduser().absolute()
        if not source.is_dir():
            raise AnalysisError("invalid_analysis_directory")
        if output.resolve().is_relative_to(source):
            raise AnalysisError("analysis_output_inside_source")
        files = _iter_source_eml_files(source)
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, AnalysisError):
            raise
        raise AnalysisError("invalid_analysis_directory") from None
    effective = config or TypeSafeConfig.from_environment()
    targets = [output / path.relative_to(source).with_suffix(".analysis.json") for path in files]
    items = [{"source": path.relative_to(source).as_posix(),
              "output": target.relative_to(output).as_posix(),
              "outcome": "not_started", "error_code": None}
             for path, target in zip(files, targets)]
    summary = {
        "schema_version": 1, "artifact_type": "directory_analysis",
        "status": "completed", "stop_reason": None,
        "counts": {}, "observed_models": {}, "usage": {},
        "items_with_unknown_usage": 0, "billing_status": "not_attempted", "items": items,
    }
    aliases: dict[str, list[int]] = {}
    for index, target in enumerate(targets):
        # Resolve parent aliases as well as case aliases before starting any work.
        names = {str(target)}
        try:
            names.add(str(target.parent.resolve() / target.name))
        except (OSError, RuntimeError):
            # Invalid destinations are refused per item by the sidecar path.
            pass
        for name in names:
            key = os.path.normcase(name).casefold()
            aliases.setdefault(key, []).append(index)
    collisions = set()
    for indices in aliases.values():
        if len(set(indices)) > 1:
            collisions.update(indices)
    for index in collisions:
        items[index].update(outcome="failed", error_code="analysis_output_collision")

    stop = asyncio.Event()
    checked = False
    workers = []
    first_outcomes = []
    throttle_streak = 0

    def stop_run(reason: str) -> None:
        if not stop.is_set() or reason == "authentication_failed":
            summary["stop_reason"] = reason
        stop.set()

    def check_preflight(*, allow_remote: bool) -> None:
        nonlocal checked
        if not checked:
            try:
                preflight(allow_remote=allow_remote)
            except AnalysisError:
                stop_run("preflight_failed")
                raise
            checked = True

    remote = TypeSafeProvider(effective, _preflight=check_preflight)
    pending = iter(index for index in range(len(files)) if index not in collisions)
    queue: asyncio.Queue[int] = asyncio.Queue(maxsize=jobs)

    def feed_one() -> None:
        index = next(pending, None)
        if index is not None:
            queue.put_nowait(index)

    for _ in range(jobs):
        feed_one()

    def record(index: int, result: dict) -> None:
        nonlocal throttle_streak
        sidecar = result.get("sidecar", {})
        reused = sidecar.get("outcome") == "reused"
        attempted = bool(result["attempts"]) and not reused
        attempted = attempted or sidecar.get("discarded_fresh_result", False)
        if attempted:
            summary["billing_status"] = "unknown"
        code = result.get("error_code") or result.get("reason")
        outcome = "reused" if reused else result["execution_status"]
        if outcome == "interrupted":
            outcome = "failed"
        if sidecar.get("outcome") == "write_failed":
            outcome, code = "failed", sidecar["error_code"]
        items[index].update(outcome=outcome, error_code=code)
        if result["execution_status"] == "succeeded":
            returned_model = result["returned_model"]
            if returned_model is not None:
                models = summary["observed_models"]
                models[returned_model] = models.get(returned_model, 0) + 1
            usage = result["usage"]
            if not usage:
                summary["items_with_unknown_usage"] += 1
            for name, value in usage.items():
                summary["usage"][name] = summary["usage"].get(name, 0) + value
        elif attempted:
            summary["items_with_unknown_usage"] += 1
        if not attempted or result["execution_status"] == "interrupted":
            return
        code = result.get("error_code")
        if len(first_outcomes) < 3:
            first_outcomes.append(code)
        throttle_streak = throttle_streak + 1 if code in _THROTTLED else 0
        if code in _AUTH:
            stop_run("authentication_failed")
            for task in workers:
                if task is not asyncio.current_task():
                    task.cancel()
        elif len(first_outcomes) == 3 and all(code in _REJECTED for code in first_outcomes):
            stop_run("request_rejected")
        elif throttle_streak >= 3:
            stop_run("provider_throttled")

    async def worker() -> None:
        while not stop.is_set():
            try:
                index = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                targets[index].parent.mkdir(parents=True, exist_ok=True)
                result = await analyze_to_sidecar(
                    files[index], targets[index], provider=provider, allow_remote=True,
                    profile_name=profile_name, focus_identity=focus_identity,
                    max_context_segments=max_context_segments, model=model,
                    config=effective, alias_max_age=alias_max_age, on_disclosure=on_disclosure,
                    _preflight=check_preflight, _provider=remote,
                )
            except asyncio.CancelledError as exc:
                result = getattr(exc, "result", None)
                if result is not None:
                    record(index, result)
                else:
                    items[index].update(outcome="failed", error_code="analysis_interrupted")
                raise
            except Exception as exc:
                code = exc.code if isinstance(exc, AnalysisError) else "analysis_item_failed"
                items[index].update(outcome="failed", error_code=code)
                if getattr(exc, "discarded_fresh_result", False):
                    summary["billing_status"] = "unknown"
                    summary["items_with_unknown_usage"] += 1
            else:
                record(index, result)
            finally:
                queue.task_done()
            if not stop.is_set():
                feed_one()
            # Even immediate fake/local completions must allow cancellation and peers.
            await asyncio.sleep(0)

    interrupted = False
    try:
        async with asyncio.TaskGroup() as group:
            for _ in range(jobs):
                workers.append(group.create_task(worker()))
    except asyncio.CancelledError:
        interrupted = True
        summary.update(status="interrupted", stop_reason="interrupted")
    if not interrupted and stop.is_set():
        summary["status"] = "stopped"
    counts = {name: 0 for name in ("succeeded", "reused", "skipped", "failed", "not_started")}
    for item in items:
        counts[item["outcome"]] += 1
    summary["counts"] = {"discovered": len(files), **counts}
    if interrupted:
        raise DirectoryAnalysisInterrupted(summary) from None
    return summary
