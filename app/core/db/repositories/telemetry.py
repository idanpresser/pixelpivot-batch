"""Best-effort telemetry persistence for pipeline_telemetry rows.

By design these writes NEVER raise to the caller. Per-tick samples (CPU%,
RAM MB, GPU%, VRAM MB) are nice-to-have. The authoritative per-batch
telemetry (duration_ms, success/failure counts) lives in ``batch_summary``
and is written by ``BatchRepository.save_summary`` — that one must succeed.

When SQLite is busy, the disk is full, or a sample tuple is malformed, this
module logs a warning and drops the affected batch of samples. Continuity of
the live converter loop is worth more than the lost samples.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Sequence

from ...logger import get_logger

log = get_logger(__name__)


def insert_telemetry(
    conn: sqlite3.Connection,
    run_id: int,
    cpu_pct: float,
    ram_mb: float,
    gpu_pct: float,
    vram_mb: float,
    auto_commit: bool = True,
) -> None:
    """Insert a single batch_telemetry sample row.

    Best-effort: on any error, logs a warning and silently drops the sample
    rather than raising. Per-tick samples are nice-to-have; authoritative
    per-batch metrics live in batch_summary.

    Args:
        conn: sqlite3.Connection for database access.
        run_id: batch_telemetry.run_id foreign key (batch_runs.id).
        cpu_pct: CPU utilization percentage (0-100).
        ram_mb: RAM usage in megabytes.
        gpu_pct: GPU utilization percentage (0-100).
        vram_mb: VRAM usage in megabytes.
        auto_commit: If True, commits the transaction on success.
    """
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                """
                INSERT INTO batch_telemetry
                    (run_id, timestamp, cpu_pct, ram_mb, gpu_pct, vram_mb)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (run_id, datetime.now(timezone.utc), cpu_pct, ram_mb, gpu_pct, vram_mb),
            )
        finally:
            cur.close()
        if auto_commit:
            conn.commit()
    except Exception as e:
        log.warning("telemetry insert dropped (run_id=%s): %s", run_id, e)


def insert_telemetry_batch(
    conn: sqlite3.Connection,
    samples: Sequence[tuple],
    auto_commit: bool = True,
) -> None:
    """Insert multiple batch_telemetry samples in one transaction.

    Best-effort: on any error, logs a warning and silently drops the entire
    batch rather than raising. Per-tick samples are nice-to-have; authoritative
    per-batch metrics live in batch_summary.

    Args:
        conn: sqlite3.Connection for database access.
        samples: Sequence of (run_id, timestamp, cpu_pct, ram_mb, gpu_pct, vram_mb)
            tuples, where run_id is batch_runs.id.
        auto_commit: If True, commits the transaction on success.
    """
    if not samples:
        return
    try:
        cur = conn.cursor()
        try:
            cur.executemany(
                """
                INSERT INTO batch_telemetry
                    (run_id, timestamp, cpu_pct, ram_mb, gpu_pct, vram_mb)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                samples,
            )
        finally:
            cur.close()
        if auto_commit:
            conn.commit()
    except Exception as e:
        log.warning("telemetry batch dropped (%d samples): %s", len(samples), e)
