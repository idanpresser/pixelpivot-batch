"""Database export and restore utilities for backup and data portability.

Provides functions to create versioned SQLite backups with automatic rotation,
and to restore databases from backup files with integrity validation.
"""

import os
import glob
import logging
import sqlite3
import shutil
import pandas as pd
from datetime import datetime
from typing import Optional
from .connection import get_connection
from ..paths import SQLITE_DB_PATH, DATASET_DIR

log = logging.getLogger(__name__)

def manage_periodic_exports(conversion_count: int):
    """Create a new SQLite backup and maintain up to two recent copies.

    Naming: pixelpivot_export_YYYYMMDD_HHMMSS_{N}conversions.db. Older
    backups beyond the two most recent are deleted. Best-effort on errors
    (logged but not raised).

    Args:
        conversion_count: Number of successful conversions in this export
            (used in the backup filename for reference).
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        from ..paths import PROJ_ROOT
        export_dir = PROJ_ROOT / "data" / "auto_exports"
        os.makedirs(export_dir, exist_ok=True)
        
        filename = f"pixelpivot_export_{timestamp}_{conversion_count}conversions.db"
        output_path = str(export_dir / filename)
        
        success = export_to_sqlite(output_path)
        if not success:
            return
            
        # Manage rotation: keep only the 2 most recent files
        existing_exports = sorted(
            glob.glob(str(export_dir / "pixelpivot_export_*.db")),
            key=os.path.getmtime,
            reverse=True
        )
        
        if len(existing_exports) > 2:
            for old_export in existing_exports[2:]:
                try:
                    os.remove(old_export)
                    log.info(f"Removed old redundant export: {os.path.basename(old_export)}")
                except Exception as e:
                    log.warning(f"Failed to remove old export {old_export}: {e}")
                    
    except Exception as e:
        log.error(f"Error managing periodic exports: {e}")


def export_to_sqlite(output_path: str) -> bool:
    """Export all analytics and batch tables to a new SQLite database file.

    Args:
        output_path: Filesystem path where the backup database will be written.
            Existing file at this path is removed first.

    Returns:
        bool: True if export succeeded, False on any error (logged).
    """
    try:
        log.info(f"Starting database export -> {output_path}")
        
        # If output_path exists, remove it first to start fresh
        if os.path.exists(output_path):
            os.remove(output_path)
            
        # Simplest way for SQLite: just copy the file
        # But to ensure consistency (if WAL is used), we can use the backup API or just pandas
        # Since we use WAL, a simple file copy might miss data in the -wal file.
        # We'll use pandas to be safe and cross-platform.
        
        with get_connection() as src_conn:
            sl_conn = sqlite3.connect(output_path)
            
            # List of tables to export
            tables = ["images", "conversions", "metrics", "quality_priors", 
                      "batch_runs", "batch_summary", "batch_errors",
                      "pipeline_runs", "pipeline_logs", "pipeline_telemetry",
                      "infra_config", "users"]
            
            for table in tables:
                try:
                    df = pd.read_sql_query(f"SELECT * FROM {table}", src_conn)
                    if not df.empty:
                        df.to_sql(table, sl_conn, index=False)
                        log.debug(f"Exported table {table} ({len(df)} rows)")
                except Exception as e:
                    log.debug(f"Skipping table {table}: {e}")
            
            sl_conn.close()
            
        log.info("Successfully exported database.")
        return True
        
    except Exception as e:
        log.error(f"Failed to export database: {e}")
        return False


def restore_from_sqlite(sqlite_path: str) -> bool:
    """Restore the main database from a backup file.

    Validates that the backup contains required tables (conversions, images,
    metrics) before overwriting. Creates a pre-restore backup of the current
    database, and restores it if the overwrite fails.

    Args:
        sqlite_path: Path to the backup SQLite database file.

    Returns:
        bool: True if restore succeeded, False if file not found, validation
            failed, or overwrite failed (all cases logged).
    """
    try:
        if not os.path.exists(sqlite_path):
            log.error(f"Restore failed: File not found at {sqlite_path}")
            return False

        log.info(f"Starting DB restoration from {sqlite_path}")
        
        # 1. Validation: Check if critical tables exist
        sl_conn = sqlite3.connect(sqlite_path)
        required_tables = {"conversions", "images", "metrics"}
        cursor = sl_conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        existing_tables = {row[0] for row in cursor.fetchall()}
        sl_conn.close()
        
        missing = required_tables - existing_tables
        if missing:
            log.error(f"Restore aborted: Backup file is missing required tables: {missing}")
            return False

        # 2. Overwrite the main DB file
        # We should probably close any active connections first, but get_connection is scoped.
        # To be safe, we'll try to replace the file.
        
        # Backup the current DB before overwriting
        backup_of_current = f"{SQLITE_DB_PATH}.pre_restore"
        if SQLITE_DB_PATH.exists():
            shutil.copy2(SQLITE_DB_PATH, backup_of_current)
            
        try:
            shutil.copy2(sqlite_path, SQLITE_DB_PATH)
            # Remove WAL files to avoid corruption if the new file is different
            for extra in [f"{SQLITE_DB_PATH}-wal", f"{SQLITE_DB_PATH}-shm"]:
                if os.path.exists(extra):
                    os.remove(extra)
            log.info("Database restoration complete (file overwrite).")
            return True
        except Exception as e:
            log.error(f"Failed to overwrite DB file: {e}")
            if os.path.exists(backup_of_current):
                shutil.copy2(backup_of_current, SQLITE_DB_PATH)
                log.info("Restored current database from pre-restore backup.")
            return False

    except Exception as e:
        log.error(f"Restoration failed: {e}")
        return False
