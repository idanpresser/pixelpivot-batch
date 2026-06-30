"""Logger Configuration — file, stream, and database handlers for PixelPivot.

Centralized logging setup with rotating file handler, stdout stream handler, and
optional database-backed handler for pipeline runs.
"""

import logging
import sys
import json
import time
from typing import Optional
from logging.handlers import RotatingFileHandler

_current_run_id = None


def set_run_id(run_id: Optional[int]):
    """Set the current pipeline run ID for DB-backed logging.

    Args:
        run_id: Integer run ID from batch_runs table, or None to disable DB logging.
    """
    global _current_run_id
    _current_run_id = run_id

class DBLogHandler(logging.Handler):
    """Handler that writes logs to the pipeline_logs table.

    Active only when ``set_run_id(...)`` has been called (legacy pipeline
    path). Best-effort: errors are swallowed so logging never crashes the
    encoder loop.
    """

    def emit(self, record):
        if _current_run_id is None:
            return

        # Avoid recursion and noise from the DB driver itself
        if record.name.startswith("core.db") or record.name.startswith("sqlite3"):
            return

        try:
            from .db.connection import get_connection

            metadata = getattr(record, "metadata", {})
            with get_connection() as conn:
                cur = conn.cursor()
                try:
                    cur.execute(
                        """INSERT INTO pipeline_logs (run_id, level, module, message, metadata_json)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            _current_run_id,
                            record.levelname,
                            record.name,
                            record.getMessage(),
                            json.dumps(metadata),
                        ),
                    )
                finally:
                    cur.close()
        except Exception:
            # Silently fail to avoid crashing the pipeline due to logging issues
            pass

import os
from datetime import datetime, timezone


class EcsJsonFormatter(logging.Formatter):
    """Single-line Elastic Common Schema JSON formatter."""

    # record attrs that are NOT extra fields
    _RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
        "trace_id", "batch", "subprocess", "performance", "message", "asctime"
    }

    def __init__(self, service_name: str = "pixelpivot"):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        out = {
            "@timestamp": datetime.fromtimestamp(record.created, timezone.utc)
            .isoformat().replace("+00:00", "Z"),
            "log.level": record.levelname,
            "message": record.getMessage(),
            "service.name": self.service_name,
            "trace.id": getattr(record, "trace_id", None),
            "log.logger": record.name,
        }
        for prefix in ("batch", "performance", "subprocess"):
            payload = getattr(record, prefix, None)
            if isinstance(payload, dict):
                for k, v in payload.items():
                    out[f"{prefix}.{k}"] = v
        if record.exc_info:
            out["error.stack_trace"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=True)


def _selected_formatter() -> logging.Formatter:
    if os.environ.get("PIXELPIVOT_LOG_FORMAT", "text").lower() == "json":
        return EcsJsonFormatter(
            service_name=os.environ.get("PIXELPIVOT_SERVICE_NAME", "pixelpivot")
        )
    return logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(filename)s:%(funcName)s] - %(message)s"
    )

_configured = False


def _configure_root_once() -> None:
    """Install file/stream/DB handlers once on the root logger.

    Previously each get_logger(name) call attached its own RotatingFileHandler
    to the named logger. On Windows, rotation's os.rename failed because other
    handlers held the file open. Moving handlers to the root and relying on
    propagation ensures exactly one RotatingFileHandler per file with no races.
    """
    global _configured
    if _configured:
        return

    formatter = _selected_formatter()

    from .paths import PROJ_ROOT
    log_file = str(PROJ_ROOT / "pixelpivot.log")
    # `delay=True` defers the first open until the first WARNING record is
    # actually emitted -- harmless and prevents holding the file across
    # tests that monkeypatch PROJ_ROOT.
    file_handler = RotatingFileHandler(
        log_file, maxBytes=1_000_000, backupCount=10, delay=True
    )
    file_handler.setLevel(logging.WARNING)
    
    from .tracing import TraceIdFilter
    _trace_filter = TraceIdFilter()
    file_handler.setFormatter(formatter)
    file_handler.addFilter(_trace_filter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(_trace_filter)

    db_handler = DBLogHandler()
    db_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    root.addHandler(file_handler)
    root.addHandler(stream_handler)
    root.addHandler(db_handler)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Acquire a child logger that propagates to the root handlers.

    Handlers are installed once on the root logger; named loggers stay
    handler-free and rely on propagation, avoiding Windows rotation races
    when multiple modules call get_logger().

    Args:
        name: Logger name (typically __name__ from calling module).

    Returns:
        A child logger that propagates to root.
    """
    _configure_root_once()
    return logging.getLogger(name)
