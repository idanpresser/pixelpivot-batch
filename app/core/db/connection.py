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

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

_engines: dict[str, "Engine"] = {}


def _db_url() -> str:
    """Resolve the SQLAlchemy URL: explicit PIXELPIVOT_DB_URL wins, else sqlite at get_db_path()."""
    import os
    url = os.getenv("PIXELPIVOT_DB_URL")
    if url:
        return url
    return f"sqlite:///{get_db_path()}"


def get_engine() -> Engine:
    """Return a process-cached engine for the current DB URL."""
    url = _db_url()
    eng = _engines.get(url)
    if eng is None:
        is_sqlite = url.startswith("sqlite")
        if is_sqlite:
            get_db_path().parent.mkdir(parents=True, exist_ok=True)
            from sqlalchemy.pool import NullPool
            poolclass = NullPool
        else:
            poolclass = None
        eng = create_engine(
            url,
            future=True,
            poolclass=poolclass,
            # one shared in-process connection for file sqlite is fine; pool for pg
            connect_args={"check_same_thread": False} if is_sqlite else {},
        )
        _engines[url] = eng
    return eng


def reset_engine_cache() -> None:
    """Dispose + drop cached engines (test isolation across DB paths)."""
    for eng in _engines.values():
        eng.dispose()
    _engines.clear()


@event.listens_for(Engine, "connect")
def _apply_sqlite_pragmas(dbapi_connection, connection_record):
    """WAL + project pragmas on every sqlite connection from any engine in the pool."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        dbapi_connection.row_factory = sqlite3.Row
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()


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


class _CompatCursor:
    """Wrap a DBAPI cursor: translate ?-paramstyle to the active dialect; keep row['col']."""

    def __init__(self, dbapi_cursor, paramstyle: str):
        self._cur = dbapi_cursor
        self._paramstyle = paramstyle

    def execute(self, sql: str, params=()):
        if self._paramstyle != "qmark" and "?" in sql:
            sql = sql.replace("?", "%s")
        return self._cur.execute(sql, params or ())

    def executemany(self, sql: str, seq):
        if self._paramstyle != "qmark" and "?" in sql:
            sql = sql.replace("?", "%s")
        return self._cur.executemany(sql, seq)

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def description(self):
        return self._cur.description

    def close(self):
        self._cur.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class _CompatConnection:
    """Legacy-shaped wrapper over a SQLAlchemy raw DBAPI connection."""

    def __init__(self, raw, paramstyle: str):
        self._raw = raw
        self._paramstyle = paramstyle

    def cursor(self) -> _CompatCursor:
        return _CompatCursor(self._raw.cursor(), self._paramstyle)

    def execute(self, sql, params=()):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()


@contextlib.contextmanager
def transaction(conn: Any):
    """Context manager for legacy transactions.

    Yields the connection. Commits on success, rolls back on exception.
    """
    try:
        with conn:
            yield conn
    except Exception as e:
        log.debug("transaction failed: %s", e)
        raise

@contextlib.contextmanager
def get_connection() -> Iterator[Any]:
    """
    Yields an engine-backed database connection.

    Task 19: Reuses a thread-local connection if already inside another
    get_connection() block, reducing open/close churn. Unit of transaction
    is the outer-most block.
    """
    engine = get_engine()
    paramstyle = engine.dialect.paramstyle
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
        raw = engine.raw_connection()
        if paramstyle != "qmark":
            try:
                from psycopg.rows import dict_row
                raw.cursor_factory = dict_row  # type: ignore[attr-defined]
            except Exception:
                pass
        c = _CompatConnection(raw, paramstyle)
        _local.conn = c
        _local.conn_path = current_path
        _local.depth = 1
        try:
            yield c
            c.commit()
        except Exception:
            try:
                c.rollback()
            except Exception as e:
                log.debug("rollback suppressed: %s", e)
            raise
        finally:
            _local.depth -= 1
            if _local.depth <= 0:
                try:
                    c.close()
                except Exception as e:
                    log.debug("close suppressed: %s", e)
                _local.conn = None
                _local.conn_path = None
                _local.depth = 0
    else:
        _local.depth += 1
        sp_name = f"sp_{_local.depth}"
        _local.conn.execute(f"SAVEPOINT {sp_name}")
        try:
            yield _local.conn
            _local.conn.execute(f"RELEASE SAVEPOINT {sp_name}")
        except Exception:
            try:
                _local.conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                _local.conn.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception as e:
                log.debug("nested rollback/release suppressed: %s", e)
            raise
        finally:
            _local.depth -= 1

def with_db_retry(
    func: Callable[..., T] | None = None,
    *,
    max_retries: int = 5,
    initial_delay: float = 0.1,
) -> Any:
    """
    Retry a database operation/function with exponential backoff on SQLite lock/busy errors.
    Can be used as a decorator or called directly with a callable.
    """
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            delay = initial_delay
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                        raise
                    if attempt == max_retries:
                        log.error(f"SQLite operation failed after {max_retries} retries due to lock: {e}")
                        raise e
                    log.warning(f"SQLite database locked/busy, retrying in {delay}s... (Attempt {attempt+1}/{max_retries}): {e}")
                    time.sleep(delay)
                    delay *= 2
                except Exception as e:
                    log.error(f"Non-retryable DB error: {e}")
                    raise
            raise sqlite3.OperationalError("Database busy retry limit reached")
        return wrapper

    if func is not None:
        return decorator(func)()
    return decorator
