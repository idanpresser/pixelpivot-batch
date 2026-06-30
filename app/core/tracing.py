"""Trace identity: one id per logical request, propagated across threads.

A ContextVar holds the current trace_id. Entry points (API/hotfolder/CLI)
call new_trace_id(prefix). A logging filter injects it onto every record and
fallback-generates a `system-` id when unset, so no record ever lacks trace.id.
"""
import contextvars
import logging
import uuid
from typing import Callable, Optional, TypeVar

_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "trace_id", default=None
)

T = TypeVar("T")


def new_trace_id(prefix: str = "") -> str:
    """Generate a fresh trace id, store it in the current context, and return it."""
    tid = f"{prefix}{uuid.uuid4().hex}"
    _trace_id.set(tid)
    return tid


def set_trace_id(tid: str) -> None:
    _trace_id.set(tid)


def get_trace_id() -> Optional[str]:
    return _trace_id.get()


def reset_trace_id() -> None:
    _trace_id.set(None)


def run_in_context(func: Callable[..., T], *args, **kwargs) -> T:
    """Run func with a *copy* of the current context (captures trace_id for threads)."""
    ctx = contextvars.copy_context()
    return ctx.run(func, *args, **kwargs)


class TraceIdFilter(logging.Filter):
    """Attach `trace_id` to every record; fallback-generate when unset."""

    def filter(self, record: logging.LogRecord) -> bool:
        tid = _trace_id.get()
        if tid is None:
            tid = new_trace_id("system-")
        record.trace_id = tid
        return True
