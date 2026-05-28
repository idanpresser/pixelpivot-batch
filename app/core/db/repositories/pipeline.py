"""Repository for pipeline_runs and associated metadata.

Manages the legacy pipeline run lifecycle (create, update phase, complete)
for the old batch orchestration system. The batch path uses batch_runs instead.
"""

import sqlite3
import json
from datetime import datetime, timezone
from typing import Optional, Any

def create_pipeline_run(
    conn: sqlite3.Connection,
    config: Any,
    dataset_root: str,
    auto_commit: bool = True,
) -> int:
    """Create a new pipeline_runs row with status='running'.

    Sets start_time=CURRENT_TIMESTAMP and serializes config to JSON.

    Args:
        conn: sqlite3.Connection for database access.
        config: Configuration object (serialized to JSON, or None).
        dataset_root: Root directory path for the dataset being processed.
        auto_commit: If True, commits the transaction on success.

    Returns:
        int: The pipeline_runs.id of the newly created row.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO pipeline_runs (start_time, status, current_phase, dataset_root, config_json)
               VALUES (?, 'running', 'init', ?, ?) RETURNING id""",
            (datetime.now(timezone.utc), dataset_root, json.dumps(config) if config is not None else None),
        )
        row = cur.fetchone()
        run_id = int(row["id"]) if row else 0
        if auto_commit:
            conn.commit()
        return run_id
    finally:
        cur.close()

def update_pipeline_run_phase(
    conn: sqlite3.Connection,
    run_id: int,
    phase: str,
    progress: Any = None,
    auto_commit: bool = True,
):
    """Update the current_phase and optional progress_json snapshot.

    Args:
        conn: sqlite3.Connection for database access.
        run_id: pipeline_runs.id to update.
        phase: New phase name (e.g. 'init', 'processing', 'complete').
        progress: Optional progress snapshot object (serialized to JSON).
        auto_commit: If True, commits the transaction on success.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE pipeline_runs
               SET current_phase = ?, progress_json = ?
               WHERE id = ?""",
            (phase, json.dumps(progress) if progress is not None else None, run_id),
        )
        if auto_commit:
            conn.commit()
    finally:
        cur.close()

def complete_pipeline_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str = "completed",
    error_message: str = None,
    auto_commit: bool = True,
):
    """Mark a pipeline run as completed or failed.

    Sets end_time=CURRENT_TIMESTAMP and status to a terminal value.

    Args:
        conn: sqlite3.Connection for database access.
        run_id: pipeline_runs.id to complete.
        status: Terminal status value (default 'completed', or 'failed').
        error_message: Optional error message on failure.
        auto_commit: If True, commits the transaction on success.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """UPDATE pipeline_runs
               SET end_time = ?, status = ?, error_message = ?
               WHERE id = ?""",
            (datetime.now(timezone.utc), status, error_message, run_id),
        )
        if auto_commit:
            conn.commit()
    finally:
        cur.close()

def get_interrupted_run(conn: sqlite3.Connection) -> Optional[dict]:
    """Fetch the most recent pipeline run with status='running' and no end_time.

    Deserializes config_json and progress_json to dicts.

    Args:
        conn: sqlite3.Connection for database access.

    Returns:
        dict with columns: id, start_time, current_phase, dataset_root,
        config_json (dict), progress_json (dict); or None if not found.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, start_time, current_phase, dataset_root, config_json, progress_json
               FROM pipeline_runs
               WHERE status = 'running' AND end_time IS NULL
               ORDER BY id DESC LIMIT 1""",
        )
        row = cur.fetchone()
        if row:
            res = dict(row)
            if res.get("config_json"):
                res["config_json"] = json.loads(res["config_json"])
            if res.get("progress_json"):
                res["progress_json"] = json.loads(res["progress_json"])
            return res
        return None
    finally:
        cur.close()

def get_pipeline_run_history(conn: sqlite3.Connection, limit: int = 10) -> list:
    """Fetch the most recent pipeline runs for UI display.

    Args:
        conn: sqlite3.Connection for database access.
        limit: Maximum number of rows to return (default 10).

    Returns:
        list[dict] with columns: id, start_time, end_time, status,
        current_phase, dataset_root, error_message. Ordered by id DESC.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, start_time, end_time, status, current_phase, dataset_root, error_message
               FROM pipeline_runs
               ORDER BY id DESC LIMIT ?""",
            (limit,),
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()
