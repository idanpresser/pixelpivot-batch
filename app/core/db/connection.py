"""
SQLite connection management for PixelPivot Batch.

Each call to ``get_connection()`` opens a fresh ``sqlite3.Connection`` with the
project's standard pragmas applied (WAL, NORMAL sync, 5 s busy-timeout, FK
enforcement). SQLite connections are microsecond-cheap to open — no pool
needed. Connections are closed on context exit; transactions auto-commit on
success and roll back on exception.

Python 3.12 deprecated the implicit datetime/date adapters that sqlite3 used
to register for free. We register our own (ISO-8601 strings) once at module
import; combined with ``detect_types=PARSE_DECLTYPES`` on each connection,
columns declared ``TIMESTAMP`` and ``DATE`` round-trip back into Python
``datetime`` / ``date`` objects automatically.
"""

from __future__ import annotations

import contextlib
import sqlite3
import threading
import functools
import time
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Callable, Any

from ..logger import get_logger
from ..paths import SQLITE_DB_PATH

log = get_logger(__name__)

_local = threading.local()


# ---------------------------------------------------------------------------
# Date / datetime <-> SQLite adapters & converters.
#
# Registration is process-global (sqlite3 keeps these in a module-level map),
# so importing this module once is sufficient for every connection — including
# in-memory test connections that don't go through _open().
# ---------------------------------------------------------------------------
def _adapt_datetime(dt: datetime) -> str:
    return dt.isoformat(sep=" ", timespec="microseconds")


def _adapt_date(d: date) -> str:
    return d.isoformat()


def _convert_timestamp(raw: bytes) -> datetime:
    # SQLite stores whatever the adapter produced. We use ISO-8601 with
    # either a space or 'T' separator; fromisoformat handles both since 3.11.
    return datetime.fromisoformat(raw.decode("utf-8"))


def _convert_date(raw: bytes) -> date:
    return date.fromisoformat(raw.decode("utf-8"))


sqlite3.register_adapter(datetime, _adapt_datetime)
sqlite3.register_adapter(date, _adapt_date)
sqlite3.register_converter("TIMESTAMP", _convert_timestamp)
sqlite3.register_converter("DATE", _convert_date)

# ---------------------------------------------------------------------------
# Pragmas applied on every fresh connection. Centralized here so test
# connections (in-memory or file-backed) inherit the same defaults via
# _configure().
# ---------------------------------------------------------------------------
# journal_mode=WAL is set once during schema bootstrap (schema.py).
_PRAGMAS: tuple[str, ...] = (
    "PRAGMA synchronous=NORMAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA foreign_keys=ON",
)


def _configure(conn: sqlite3.Connection) -> None:
    """Apply project pragmas to a freshly-opened connection."""
    cur = conn.cursor()
    try:
        for stmt in _PRAGMAS:
            cur.execute(stmt)
    finally:
        cur.close()


def get_db_path() -> Path:
    """Dynamically resolve the database path, checking environment variables.

    This prevents test isolation issues where reloaded modules or thread pools
    access stale DB paths.
    """
    import os
    env_path = os.getenv("PIXELPIVOT_DB_PATH")
    if env_path:
        return Path(env_path)
    db_url = os.getenv("DATABASE_URL")
    if db_url and db_url.startswith("sqlite:///"):
        return Path(db_url.replace("sqlite:///", ""))
    return SQLITE_DB_PATH


def _open(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a fresh SQLite connection with pragmas applied."""
    target = db_path if db_path is not None else get_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(target),
        check_same_thread=False,
        timeout=5.0,
        isolation_level="DEFERRED",
        detect_types=sqlite3.PARSE_DECLTYPES,
    )
    conn.row_factory = sqlite3.Row
    _configure(conn)
    return conn


@contextlib.contextmanager
def transaction(conn: sqlite3.Connection):
    """Context manager for SQLite transactions.

    Yields the connection. Commits on success, rolls back on exception.
    """
    try:
        # sqlite3.Connection itself is a context manager that handles
        # transactions (commit/rollback) but NOT closing.
        with conn:
            yield conn
    except Exception as e:
        log.debug("transaction failed: %s", e)
        raise

@contextlib.contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """
    Yields a SQLite connection bound to ``SQLITE_DB_PATH``.

    Task 19: Reuses a thread-local connection if already inside another
    get_connection() block, reducing open/close churn. Unit of transaction
    is the outer-most block.
    """
    current_path = get_db_path()
    if (
        not hasattr(_local, "conn")
        or _local.conn is None
        or getattr(_local, "conn_path", None) != current_path
    ):
        if getattr(_local, "conn", None) is not None:
            try:
                _local.conn.close()
            except Exception:
                pass
        conn = _open()
        _local.conn = conn
        _local.conn_path = current_path
        _local.depth = 1
        try:
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception as e:
                log.debug("rollback suppressed: %s", e)
            raise
        finally:
            _local.depth -= 1
            if _local.depth <= 0:
                try:
                    conn.close()
                except Exception as e:
                    log.debug("close suppressed: %s", e)
                _local.conn = None
                _local.conn_path = None
                _local.depth = 0
    else:
        _local.depth += 1
        try:
            yield _local.conn
        finally:
            _local.depth -= 1

def with_db_retry(max_retries: int = 3, initial_delay: float = 0.5):
    """
    Exponential backoff retry decorator for SQLite operations.
    Handles transient lock contention (database is locked).
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = initial_delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    # SQLite raises OperationalError when the database is locked
                    if "locked" in str(e).lower() or "busy" in str(e).lower():
                        if attempt == max_retries:
                            log.error(f"SQLite operation failed after {max_retries} retries due to lock: {e}")
                            raise e
                        log.warning(f"SQLite database locked, retrying in {delay}s... (Attempt {attempt+1}/{max_retries}): {e}")
                        time.sleep(delay)
                        delay *= 2
                        continue
                    raise e
                except Exception as e:
                    log.error(f"Non-retryable DB error in {func.__name__}: {e}")
                    raise e
        return wrapper
    return decorator
