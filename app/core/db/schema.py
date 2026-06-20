"""Schema bootstrap for the PixelPivot Batch SQLite database.

All tables are SQLite-only DDL. Legacy analytics tables (images, conversions,
metrics, quality_priors, pipeline_*) are preserved so the legacy modules can
still write into the same DB if the user installs the optional `[legacy]`
dependencies, but they are not exercised by the batch path.
"""

from __future__ import annotations

import sqlite3

from ..logger import get_logger
from .connection import get_connection

log = get_logger(__name__)


_DDL_STATEMENTS: tuple[str, ...] = (
    # --- core batch tables (active) ---
    """
    CREATE TABLE IF NOT EXISTS batch_runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source_dir      TEXT    NOT NULL,
        target_dir      TEXT    NOT NULL,
        target_format   TEXT    NOT NULL,
        tool            TEXT    NOT NULL,
        trigger_type    TEXT    NOT NULL,
        status          TEXT    NOT NULL,
        total_images    INTEGER DEFAULT 0,
        heuristic_version TEXT,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at    TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS batch_summary (
        batch_id        INTEGER PRIMARY KEY REFERENCES batch_runs(id) ON DELETE CASCADE,
        duration_ms     DOUBLE PRECISION,
        cpu_avg_pct     DOUBLE PRECISION,
        cpu_peak_pct    DOUBLE PRECISION,
        ram_peak_mb     DOUBLE PRECISION,
        yield_mb_sec    DOUBLE PRECISION,
        savings_pct     DOUBLE PRECISION,
        success_count   INTEGER,
        failure_count   INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS batch_errors (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id    INTEGER NOT NULL REFERENCES batch_runs(id) ON DELETE CASCADE,
        input_path  TEXT,
        error       TEXT NOT NULL,
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # Per-tick sampling for batch runs. Keyed on batch_runs(id) (NOT the
    # legacy pipeline_runs.id), so the batch path can persist its samples
    # without violating the pipeline_telemetry FK. Task 020.
    """
    CREATE TABLE IF NOT EXISTS batch_telemetry (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          INTEGER NOT NULL REFERENCES batch_runs(id) ON DELETE CASCADE,
        timestamp       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        cpu_pct         DOUBLE PRECISION,
        ram_mb          DOUBLE PRECISION
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS calibration_results (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        batch_id        INTEGER NOT NULL REFERENCES batch_runs(id) ON DELETE CASCADE,
        input_path      TEXT    NOT NULL,
        target_ssim     DOUBLE PRECISION,
        quality_found   DOUBLE PRECISION,
        iterations      INTEGER,
        data_json       TEXT,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,

    # --- legacy analytics tables (retained, not exercised by batch path) ---
    """
    CREATE TABLE IF NOT EXISTS images (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        filename     TEXT    NOT NULL,
        category     TEXT    NOT NULL,
        arrival_time TIMESTAMP,
        image_uuid   TEXT,
        width        INTEGER,
        height       INTEGER,
        size_bytes   BIGINT,
        format       TEXT,
        sha256       TEXT,
        is_corrupt   BOOLEAN NOT NULL DEFAULT 0,
        UNIQUE(filename, category)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversions (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        image_id          INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
        format            TEXT    NOT NULL,
        tool              TEXT    NOT NULL,
        quality           DOUBLE PRECISION,
        parameters        TEXT,
        duration_ms       DOUBLE PRECISION,
        cpu_avg_pct       DOUBLE PRECISION,
        cpu_peak_pct      DOUBLE PRECISION,
        ram_peak_mb       DOUBLE PRECISION,
        gpu_peak_pct      DOUBLE PRECISION,
        vram_peak_mb      DOUBLE PRECISION,
        output_size_bytes BIGINT,
        savings_pct       DOUBLE PRECISION,
        calib_ssim        DOUBLE PRECISION,
        calib_method      TEXT,
        success           BOOLEAN NOT NULL DEFAULT 0,
        error_message     TEXT,
        created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(image_id, format, tool)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS metrics (
        conversion_id INTEGER PRIMARY KEY REFERENCES conversions(id) ON DELETE CASCADE,
        ssim          DOUBLE PRECISION,
        ms_ssim       DOUBLE PRECISION,
        psnr_db       DOUBLE PRECISION,
        delta_e       DOUBLE PRECISION,
        lpips         DOUBLE PRECISION,
        dists         DOUBLE PRECISION,
        meta_score    DOUBLE PRECISION,
        lcp_ms        DOUBLE PRECISION,
        lcp_method    TEXT,
        compute_ms    DOUBLE PRECISION,
        updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_priors (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        category     TEXT NOT NULL,
        format       TEXT NOT NULL,
        tool         TEXT NOT NULL,
        mean_quality DOUBLE PRECISION NOT NULL,
        avg_bpp      DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        avg_slope    DOUBLE PRECISION NOT NULL DEFAULT 0.0,
        sample_count INTEGER NOT NULL DEFAULT 0,
        UNIQUE(category, format, tool)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_runs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        start_time      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        end_time        TIMESTAMP,
        status          TEXT NOT NULL,
        current_phase   TEXT,
        dataset_root    TEXT,
        config_json     TEXT,
        progress_json   TEXT,
        error_message   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_logs (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          INTEGER NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
        timestamp       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        level           TEXT NOT NULL,
        module          TEXT NOT NULL,
        message         TEXT NOT NULL,
        metadata_json   TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_telemetry (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          INTEGER NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
        timestamp       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        cpu_pct         DOUBLE PRECISION,
        ram_mb          DOUBLE PRECISION,
        gpu_pct         DOUBLE PRECISION,
        vram_mb         DOUBLE PRECISION
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS infra_config (
        config_key      TEXT PRIMARY KEY,
        config_value    TEXT,
        updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS users (
        username        TEXT PRIMARY KEY,
        password_hash   TEXT NOT NULL,
        full_name       TEXT,
        is_active       BOOLEAN NOT NULL DEFAULT 1,
        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
)


def init_db(conn: sqlite3.Connection | None = None) -> None:
    """Create all tables. Idempotent. Includes integrity check (Task 12)."""
    if conn is None:
        with get_connection() as connection:
            _verify_integrity(connection)
            _create_tables(connection)
    else:
        _verify_integrity(conn)
        _create_tables(conn)


def _verify_integrity(conn: sqlite3.Connection) -> None:
    """Performs PRAGMA integrity_check and raises RuntimeError if it fails."""
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA integrity_check")
        res = cur.fetchone()
        if not res or res[0].lower() != "ok":
            log.critical(f"Database integrity check failed: {res}")
            raise RuntimeError(f"Database integrity check failed: {res}")
        log.info("Database integrity check: OK")
    finally:
        cur.close()


def _create_tables(conn: sqlite3.Connection) -> None:
    log.info("Initialising SQLite schema...")
    cur = conn.cursor()
    try:
        # Apply project pragmas in case the caller passed a raw connection
        # (tests pass in-memory or temp-file connections without going through
        # _configure() in connection.py).
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA foreign_keys=ON")
        for stmt in _DDL_STATEMENTS:
            cur.execute(stmt)
        
        # Migration: Add heuristic_version if it's missing (Task 04)
        cur.execute("PRAGMA table_info('batch_runs')")
        columns = [row[1] for row in cur.fetchall()]
        if "heuristic_version" not in columns:
            log.info("Migrating batch_runs: adding heuristic_version column")
            cur.execute("ALTER TABLE batch_runs ADD COLUMN heuristic_version TEXT")

        # Migration: Drop GPU columns from batch_summary and batch_telemetry.
        # Idempotent via PRAGMA table_info gating. Requires SQLite >= 3.35
        # for DROP COLUMN; Python 3.12+ ships with 3.45+. The pre-removal
        # "Task 21" migration that ADDED these columns to old DBs has been
        # deleted -- the order would otherwise be add/drop/add/... on every
        # bootstrap. CPU-only deployment target; see CHANGELOG entry for
        # 2026-05-29 GPU removal.
        cur.execute("PRAGMA table_info('batch_summary')")
        summary_cols = {row[1] for row in cur.fetchall()}
        for col in ("gpu_peak_pct", "vram_peak_mb"):
            if col in summary_cols:
                log.info("Migrating batch_summary: dropping %s", col)
                cur.execute(f"ALTER TABLE batch_summary DROP COLUMN {col}")

        cur.execute("PRAGMA table_info('batch_telemetry')")
        tel_cols = {row[1] for row in cur.fetchall()}
        for col in ("gpu_pct", "vram_mb"):
            if col in tel_cols:
                log.info("Migrating batch_telemetry: dropping %s", col)
                cur.execute(f"ALTER TABLE batch_telemetry DROP COLUMN {col}")

        conn.commit()
    finally:
        cur.close()
    log.info("SQLite schema initialised.")
