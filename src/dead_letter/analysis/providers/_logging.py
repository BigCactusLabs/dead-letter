"""Request-local suppression at SDK/HTTP log sources, before any handlers run."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock

_ACTIVE = ContextVar("dead_letter_private_provider_call", default=False)
_LOCK = RLock()
_ROOTS = ("typesafe_sdk", "httpx", "httpx2", "httpcore", "httpcore2")


class _PrivateCallFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not _ACTIVE.get()


_FILTER = _PrivateCallFilter()


def install_filters() -> None:
    """Also cover loggers loaded by optional HTTP modules on first SDK import.

    Filters stay attached but are inert outside this call's context. Do not
    change global logging levels, handlers, environment, or other threads' logs.
    The pinned SDK emits all wire/body logs at the typesafe_sdk source logger.
    """
    with _LOCK:
        names = set(_ROOTS)
        # HTTP transports may create these loggers lazily.
        for root in ("httpcore", "httpcore2"):
            names.update(f"{root}.{part}" for part in (
                "connection", "connection_pool", "http11", "http2", "proxy", "socks",
            ))
        names.update(name for name in tuple(logging.Logger.manager.loggerDict)
                     if any(name.startswith(root + ".") for root in _ROOTS))
        for name in names:
            logger = logging.getLogger(name)
            if _FILTER not in logger.filters:
                logger.addFilter(_FILTER)


@contextmanager
def private_provider_logs():
    """Enter before SDK import; TYPESAFE_LOG_LEVEL cannot bypass source filters."""
    install_filters()
    token = _ACTIVE.set(True)
    try:
        yield
    finally:
        _ACTIVE.reset(token)
