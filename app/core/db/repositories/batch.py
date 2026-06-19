"""SQLite repository for batch run lifecycle + summary persistence.

All writes here are must-succeed: lifecycle (create/update_status) and the
end-of-batch summary (duration_ms is the primary telemetry signal). The
per-tick samples in pipeline_telemetry are best-effort — see
``app/core/db/repositories/telemetry.py``.
"""

from __future__ import annotations

import sqlite3
import json
from datetime import datetime
from typing import Optional

from ... import config
from ...logger import get_logger

log = get_logger(__name__)


class BatchRepository:
    """Manages batch_runs and batch_summary table persistence.

    Provides methods for creating runs, tracking status, persisting per-batch
    summaries, storing per-file errors, and retrieving calibration results.
    """

    def create_run(
        self,
        conn: sqlite3.Connection,
        source_dir: str,
        target_dir: str,
        target_format: str,
        tool: str,
        trigger_type: str,
        heuristic_version: Optional[str] = None,
    ) -> int:
        """Insert a new batch run row and return its id.

        Args:
            conn: sqlite3.Connection for database access.
            source_dir: Input directory path.
            target_dir: Output directory path.
            target_format: Target image format (e.g. 'webp', 'avif').
            tool: Converter tool name (e.g. 'magick', 'ffmpeg').
            trigger_type: How the batch was triggered (e.g. 'api', 'hot_folder').
            heuristic_version: Optional version of heuristic table used.

        Returns:
            int: The id of the newly created batch_runs row.
        """
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO batch_runs (
                    source_dir, target_dir, target_format, tool, trigger_type, status, heuristic_version
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (source_dir, target_dir, target_format, tool, trigger_type, "running", heuristic_version),
            )
            row = cur.fetchone()
            return int(row["id"]) if row else 0
        finally:
            cur.close()

    def reap_stale_running(self, conn: sqlite3.Connection) -> int:
        """Transition all 'running' batches to 'interrupted' state.

        Called on startup to clear ghost runs left by a process crash/restart.
        Sets completed_at=CURRENT_TIMESTAMP for each affected row.

        Args:
            conn: sqlite3.Connection for database access.

        Returns:
            int: The number of batch_runs rows transitioned to 'interrupted'.
        """
        cur = conn.cursor()
        try:
            cur.execute(
                """
                UPDATE batch_runs
                SET status = 'interrupted', completed_at = CURRENT_TIMESTAMP
                WHERE status = 'running'
                """
            )
            return cur.rowcount
        finally:
            cur.close()

    def update_status(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        status: str,
        total_images: Optional[int] = None,
    ) -> None:
        """Update batch_runs status and optionally total_images.

        Sets completed_at=CURRENT_TIMESTAMP when status transitions to
        'completed' or 'failed' (terminal states).

        Args:
            conn: sqlite3.Connection for database access.
            run_id: batch_runs.id to update.
            status: New status value (e.g. 'running', 'completed', 'failed').
            total_images: Optional total image count for this batch.
        """
        clauses = ["status = ?"]
        params: list = [status]

        if status in ("completed", "failed"):
            clauses.append("completed_at = ?")
            params.append(datetime.now())

        if total_images is not None:
            clauses.append("total_images = ?")
            params.append(total_images)

        params.append(run_id)
        cur = conn.cursor()
        try:
            cur.execute(f"UPDATE batch_runs SET {', '.join(clauses)} WHERE id = ?", tuple(params))
        finally:
            cur.close()

    def get_run(self, conn: sqlite3.Connection, run_id: int) -> Optional[dict]:
        """Fetch a single batch_runs row by id.

        Args:
            conn: sqlite3.Connection for database access.
            run_id: batch_runs.id to retrieve.

        Returns:
            dict with batch_runs columns, or None if not found.
        """
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM batch_runs WHERE id = ?", (run_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            cur.close()

    def get_summary(self, conn: sqlite3.Connection, batch_id: int) -> Optional[dict]:
        """Fetch a single batch_summary row by batch_id.

        Args:
            conn: sqlite3.Connection for database access.
            batch_id: batch_summary.batch_id to retrieve.

        Returns:
            dict with batch_summary columns, or None if not found.
        """
        cur = conn.cursor()
        try:
            cur.execute("SELECT * FROM batch_summary WHERE batch_id = ?", (batch_id,))
            row = cur.fetchone()
            return dict(row) if row else None
        finally:
            cur.close()

    def get_all_runs(self, conn: sqlite3.Connection) -> list[dict]:
        """Fetch recent batch_runs with summary fields joined (up to 100 rows).

        Columns are explicitly aliased (run_id, duration_ms, etc.) to avoid
        collisions with r.id from batch_runs. Ordered by created_at DESC.

        Args:
            conn: sqlite3.Connection for database access.

        Returns:
            list[dict] with aliased columns: run_id, status, target_format, tool,
            trigger_type, total_images, created_at, completed_at, duration_ms,
            success_count, failure_count, cpu_avg_pct, cpu_peak_pct, ram_peak_mb,
            yield_mb_sec, savings_pct.
        """
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT  r.id            AS run_id,
                        r.status        AS status,
                        r.target_format AS target_format,
                        r.tool          AS tool,
                        r.trigger_type  AS trigger_type,
                        r.total_images  AS total_images,
                        r.created_at    AS created_at,
                        r.completed_at  AS completed_at,
                        COALESCE(s.duration_ms,   0) AS duration_ms,
                        COALESCE(s.success_count, 0) AS success_count,
                        COALESCE(s.failure_count, 0) AS failure_count,
                        s.cpu_avg_pct   AS cpu_avg_pct,
                        s.cpu_peak_pct  AS cpu_peak_pct,
                        s.ram_peak_mb   AS ram_peak_mb,
                        s.yield_mb_sec  AS yield_mb_sec,
                        s.savings_pct   AS savings_pct
                FROM batch_runs r
                LEFT JOIN batch_summary s ON r.id = s.batch_id
                ORDER BY r.created_at DESC
                LIMIT 100
                """
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            cur.close()

    def save_summary(
        self,
        conn: sqlite3.Connection,
        batch_id: int,
        duration_ms: float,
        cpu_avg_pct: float,
        cpu_peak_pct: float,
        ram_peak_mb: float,
        yield_mb_sec: float,
        savings_pct: float,
        success_count: int,
        failure_count: int,
    ) -> None:
        """Insert or update a batch_summary row.

        Called once per batch when it completes to persist aggregated metrics.
        Uses SQLite UPSERT to idempotently update an existing row.

        Args:
            conn: sqlite3.Connection for database access.
            batch_id: batch_summary.batch_id (PRIMARY KEY).
            duration_ms: Total batch runtime in milliseconds.
            cpu_avg_pct: Average CPU utilization (0-100).
            cpu_peak_pct: Peak CPU utilization (0-100).
            ram_peak_mb: Peak RAM usage in megabytes.
            yield_mb_sec: Throughput (megabytes per second).
            savings_pct: Compression ratio savings (0-100).
            success_count: Number of successful image conversions.
            failure_count: Number of failed conversions.
        """
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO batch_summary (
                    batch_id, duration_ms, cpu_avg_pct, cpu_peak_pct, ram_peak_mb,
                    yield_mb_sec, savings_pct, success_count, failure_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(batch_id) DO UPDATE SET
                    duration_ms   = excluded.duration_ms,
                    cpu_avg_pct   = excluded.cpu_avg_pct,
                    cpu_peak_pct  = excluded.cpu_peak_pct,
                    ram_peak_mb   = excluded.ram_peak_mb,
                    yield_mb_sec  = excluded.yield_mb_sec,
                    savings_pct   = excluded.savings_pct,
                    success_count = excluded.success_count,
                    failure_count = excluded.failure_count
                """,
                (
                    batch_id, duration_ms, cpu_avg_pct, cpu_peak_pct, ram_peak_mb,
                    yield_mb_sec, savings_pct, success_count, failure_count,
                ),
            )
        finally:
            cur.close()

    def save_errors(
        self, conn: sqlite3.Connection, batch_id: int, errors: list[dict]
    ) -> None:
        """Insert batch_errors rows for per-file conversion failures.

        Each error dict should have keys: "path" (input file path) and "error"
        (error message). Empty list is a no-op.

        Args:
            conn: sqlite3.Connection for database access.
            batch_id: batch_errors.batch_id foreign key.
            errors: list[dict] with "path" and "error" keys.
        """
        if not errors:
            return
        cur = conn.cursor()
        try:
            cur.executemany(
                "INSERT INTO batch_errors (batch_id, input_path, error) "
                "VALUES (?, ?, ?)",
                [(batch_id, e.get("path"), str(e.get("error", "unknown"))) for e in errors],
            )
        finally:
            cur.close()

    def get_errors(
        self, conn: sqlite3.Connection, batch_id: int, limit: int = 100
    ) -> list[dict]:
        """Fetch batch_errors rows for a given batch.

        Args:
            conn: sqlite3.Connection for database access.
            batch_id: batch_errors.batch_id to filter by.
            limit: Maximum number of error rows to return (default 100).

        Returns:
            list[dict] with columns: input_path, error, created_at.
        """
        cur = conn.cursor()
        try:
            cur.execute(
                """
                SELECT input_path, error, created_at 
                FROM batch_errors 
                WHERE batch_id = ? 
                ORDER BY id 
                LIMIT ?
                """,
                (batch_id, limit),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            cur.close()

    def save_calibration_result(
        self,
        conn: sqlite3.Connection,
        batch_id: int,
        input_path: str,
        target_ssim: float,
        quality_found: float,
        iterations: int,
        data: list[dict],
    ) -> None:
        """Insert a calibration_results row.

        Persists detailed quality calibration data for per-image analysis.

        Args:
            conn: sqlite3.Connection for database access.
            batch_id: calibration_results.batch_id foreign key.
            input_path: Path to the input image file.
            target_ssim: Target SSIM value for calibration.
            quality_found: Quality setting that achieved target_ssim.
            iterations: Number of binary-search iterations performed.
            data: list[dict] with per-iteration attempt details (serialized as JSON).

        No-op when ``config.CALIBRATION_ENABLED`` is False (the default): the
        table and this method are kept intact but inert — quality is resolved
        heuristically, so there is nothing to persist.
        """
        if not config.CALIBRATION_ENABLED:
            log.debug("Calibration disabled; skipping calibration_results write for %s", input_path)
            return

        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO calibration_results (
                    batch_id, input_path, target_ssim, quality_found, iterations, data_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch_id,
                    input_path,
                    target_ssim,
                    quality_found,
                    iterations,
                    json.dumps(data),
                ),
            )
        finally:
            cur.close()

    def get_calibration_results(self, conn: sqlite3.Connection, batch_id: int) -> list[dict]:
        """Fetch all calibration_results rows for a given batch.

        Args:
            conn: sqlite3.Connection for database access.
            batch_id: calibration_results.batch_id to filter by.

        Returns:
            list[dict] with calibration_results columns.
        """
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT * FROM calibration_results WHERE batch_id = ?",
                (batch_id,),
            )
            return [dict(row) for row in cur.fetchall()]
        finally:
            cur.close()

    def export_calibration_data(self, conn: sqlite3.Connection, batch_id: int) -> list[dict]:
        """Fetch calibration results with data_json deserialized to 'attempts'.

        Returns an empty list when no results exist. Modifies returned dicts
        in-place to unpack data_json into an "attempts" key.

        Args:
            conn: sqlite3.Connection for database access.
            batch_id: calibration_results.batch_id to filter by.

        Returns:
            list[dict] with calibration_results columns plus deserialized
            "attempts" (replacing "data_json").
        """
        results = self.get_calibration_results(conn, batch_id)
        for r in results:
            if r.get("data_json"):
                try:
                    r["attempts"] = json.loads(r["data_json"])
                except Exception:
                    r["attempts"] = []
                del r["data_json"]
        return results
