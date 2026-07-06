"""SQLite database layer for PixelPivot Batch.

Public API:
    - get_connection() — context manager yielding a configured sqlite3.Connection
    - init_db()        — idempotent schema bootstrap (call once on startup)
    - insert_telemetry / insert_telemetry_batch — best-effort sample writes
"""

from .connection import get_connection, transaction, DBConnection
from .repositories.telemetry import insert_telemetry, insert_telemetry_batch
from .schema import init_db

__all__ = [
    "get_connection",
    "transaction",
    "init_db",
    "insert_telemetry",
    "insert_telemetry_batch",
    "DBConnection",
]
