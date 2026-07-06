"""Schema bootstrap for the PixelPivot Batch SQLite database.

All tables are SQLite-only DDL. Legacy analytics tables (images, conversions,
metrics, quality_priors, pipeline_*) are preserved so the legacy modules can
still write into the same DB if the user installs the optional `[legacy]`
dependencies, but they are not exercised by the batch path.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..logger import get_logger
from .connection import get_connection

log = get_logger(__name__)


EXPECTED_TABLES = {
    "batch_runs", "batch_summary", "batch_errors", "batch_telemetry",
    "calibration_results", "images", "conversions", "metrics", "quality_priors",
    "pipeline_runs", "pipeline_logs", "pipeline_telemetry", "infra_config", "users",
}

_SHARED_DDL_TEMPLATE = """
CREATE TABLE IF NOT EXISTS batch_runs (
    id              {PK_AUTO},
    source_dir      TEXT    NOT NULL,
    target_dir      TEXT    NOT NULL,
    target_format   TEXT    NOT NULL,
    tool            TEXT    NOT NULL,
    category        TEXT    NOT NULL DEFAULT 'general',
    trigger_type    TEXT    NOT NULL,
    status          TEXT    NOT NULL,
    total_images    INTEGER DEFAULT 0,
    heuristic_version TEXT,
    priority        INTEGER NOT NULL DEFAULT 0,
    sample          INTEGER,
    input_files     TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at    TIMESTAMP
);

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
);

CREATE TABLE IF NOT EXISTS batch_errors (
    id              {PK_AUTO},
    batch_id        INTEGER NOT NULL REFERENCES batch_runs(id) ON DELETE CASCADE,
    input_path      TEXT,
    error           TEXT NOT NULL,
    is_dlq          BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS batch_telemetry (
    id              {PK_AUTO},
    run_id          INTEGER NOT NULL REFERENCES batch_runs(id) ON DELETE CASCADE,
    timestamp       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cpu_pct         DOUBLE PRECISION,
    ram_mb          DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS calibration_results (
    id              {PK_AUTO},
    batch_id        INTEGER NOT NULL REFERENCES batch_runs(id) ON DELETE CASCADE,
    input_path      TEXT    NOT NULL,
    target_ssim     DOUBLE PRECISION,
    quality_found   DOUBLE PRECISION,
    iterations      INTEGER,
    data_json       TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS images (
    id              {PK_AUTO},
    filename        TEXT    NOT NULL,
    category        TEXT    NOT NULL,
    arrival_time    TIMESTAMP,
    image_uuid      TEXT,
    width           INTEGER,
    height          INTEGER,
    size_bytes      BIGINT,
    format          TEXT,
    sha256          TEXT,
    is_corrupt      BOOLEAN NOT NULL DEFAULT FALSE,
    UNIQUE(filename, category)
);

CREATE TABLE IF NOT EXISTS conversions (
    id                {PK_AUTO},
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
    success           BOOLEAN NOT NULL DEFAULT FALSE,
    error_message     TEXT,
    created_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(image_id, format, tool)
);

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
);

CREATE TABLE IF NOT EXISTS quality_priors (
    id              {PK_AUTO},
    category        TEXT NOT NULL,
    format          TEXT NOT NULL,
    tool            TEXT NOT NULL,
    mean_quality    DOUBLE PRECISION NOT NULL,
    avg_bpp         DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    avg_slope       DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    sample_count    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(category, format, tool)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              {PK_AUTO},
    start_time      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    end_time        TIMESTAMP,
    status          TEXT NOT NULL,
    current_phase   TEXT,
    dataset_root    TEXT,
    config_json     TEXT,
    progress_json   TEXT,
    error_message   TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_logs (
    id              {PK_AUTO},
    run_id          INTEGER NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    timestamp       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    level           TEXT NOT NULL,
    module          TEXT NOT NULL,
    message         TEXT NOT NULL,
    metadata_json   TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_telemetry (
    id              {PK_AUTO},
    run_id          INTEGER NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
    timestamp       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cpu_pct         DOUBLE PRECISION,
    ram_mb          DOUBLE PRECISION,
    gpu_pct         DOUBLE PRECISION,
    vram_mb         DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS infra_config (
    config_key      TEXT PRIMARY KEY,
    config_value    TEXT,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS users (
    username        TEXT PRIMARY KEY,
    password_hash   TEXT NOT NULL,
    full_name       TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


def _ddl_for(dialect_name: str) -> str:
    if dialect_name.startswith("postgres"):
        pk_auto = "SERIAL PRIMARY KEY"
    else:
        pk_auto = "INTEGER PRIMARY KEY AUTOINCREMENT"
    return _SHARED_DDL_TEMPLATE.format(PK_AUTO=pk_auto)


_SQLITE_DDL = _ddl_for("sqlite")
_POSTGRES_DDL = _ddl_for("postgres")


def init_db(conn: Any = None) -> None:
    """Create all tables. Idempotent. Includes integrity check (Task 12)."""
    if conn is None:
        with get_connection() as connection:
            _verify_integrity(connection)
            _create_tables(connection)
    else:
        _verify_integrity(conn)
        _create_tables(conn)


def _verify_integrity(conn: Any) -> None:
    """Performs integrity check. sqlite-specific PRAGMA or standard postgres validation."""
    from .connection import get_connection_dialect
    dialect = get_connection_dialect(conn)
    if not dialect.startswith("sqlite"):
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1")
            cur.fetchone()
            log.info("Database integrity check: OK")
        finally:
            cur.close()
        return

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


def _get_table_columns(cur: Any, table_name: str, dialect: str) -> list[str]:
    if dialect.startswith("sqlite"):
        cur.execute(f"PRAGMA table_info('{table_name}')")
        return [row[1] for row in cur.fetchall()]
    else:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            (table_name.lower(),)
        )
        return [row["column_name"] for row in cur.fetchall()]


def _create_tables(conn: Any) -> None:
    from .connection import get_connection_dialect
    dialect = get_connection_dialect(conn)
    ddl = _ddl_for(dialect)
    log.info(f"Initialising {dialect} schema...")
    cur = conn.cursor()
    try:
        if dialect.startswith("sqlite"):
            # Apply project pragmas in case the caller passed a raw connection
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            
        for stmt in [s for s in ddl.split(";") if s.strip()]:
            cur.execute(stmt)
        
        # Unified migrations for both SQLite and Postgres
        columns = _get_table_columns(cur, "batch_runs", dialect)
        if "heuristic_version" not in columns:
            log.info("Migrating batch_runs: adding heuristic_version column")
            cur.execute("ALTER TABLE batch_runs ADD COLUMN heuristic_version TEXT")
        if "priority" not in columns:
            log.info("Migrating batch_runs: adding priority column")
            cur.execute("ALTER TABLE batch_runs ADD COLUMN priority INTEGER NOT NULL DEFAULT 0")
        if "category" not in columns:
            log.info("Migrating batch_runs: adding category column")
            cur.execute("ALTER TABLE batch_runs ADD COLUMN category TEXT NOT NULL DEFAULT 'general'")
        if "sample" not in columns:
            log.info("Migrating batch_runs: adding sample column")
            cur.execute("ALTER TABLE batch_runs ADD COLUMN sample INTEGER")
        if "input_files" not in columns:
            log.info("Migrating batch_runs: adding input_files column")
            cur.execute("ALTER TABLE batch_runs ADD COLUMN input_files TEXT")

        err_columns = _get_table_columns(cur, "batch_errors", dialect)
        if "is_dlq" not in err_columns:
            log.info("Migrating batch_errors: adding is_dlq column")
            cur.execute("ALTER TABLE batch_errors ADD COLUMN is_dlq BOOLEAN DEFAULT FALSE")

        summary_cols = set(_get_table_columns(cur, "batch_summary", dialect))
        for col in ("gpu_peak_pct", "vram_peak_mb"):
            if col in summary_cols:
                log.info("Migrating batch_summary: dropping %s", col)
                cur.execute(f"ALTER TABLE batch_summary DROP COLUMN {col}")

        tel_cols = set(_get_table_columns(cur, "batch_telemetry", dialect))
        for col in ("gpu_pct", "vram_mb"):
            if col in tel_cols:
                log.info("Migrating batch_telemetry: dropping %s", col)
                cur.execute(f"ALTER TABLE batch_telemetry DROP COLUMN {col}")

        conn.commit()
    finally:
        cur.close()
    log.info(f"{dialect} schema initialised.")

