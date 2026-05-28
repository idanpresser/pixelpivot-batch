"""Repository for metrics table persistence and quality score updates.

Handles updates to quality metrics (SSIM, PSNR, perceptual scores) and LCP
(Largest Contentful Paint) timing. Retrieves pending tasks for metric
computation across conversions.
"""

import sqlite3
from typing import List, Optional

_METRIC_COLUMNS = frozenset(
    {"ssim", "ms_ssim", "psnr_db", "delta_e", "lpips", "dists", "meta_score", "lcp_ms", "compute_ms"}
)

def update_single_metric(
    conn: sqlite3.Connection,
    conversion_id: int,
    column: str,
    value,
    auto_commit: bool = True,
):
    """Update a single metric column and set updated_at timestamp.

    Uses whitelist of allowed column names to prevent SQL injection.

    Args:
        conn: sqlite3.Connection for database access.
        conversion_id: metrics.conversion_id to update.
        column: Column name from _METRIC_COLUMNS (ssim, ms_ssim, psnr_db, etc.).
        value: New value for the column.
        auto_commit: If True, commits the transaction on success.

    Raises:
        ValueError: If column is not in _METRIC_COLUMNS.
    """
    if column not in _METRIC_COLUMNS:
        raise ValueError(f"Invalid metric column: '{column}'. Allowed: {_METRIC_COLUMNS}")

    # Whitelisted column name is safe for f-string
    query = f"UPDATE metrics SET {column} = ?, updated_at = CURRENT_TIMESTAMP WHERE conversion_id = ?"
    
    cur = conn.cursor()
    try:
        cur.execute(query, (value, conversion_id))
        if auto_commit:
            conn.commit()
    finally:
        cur.close()

def update_lcp_metric(
    conn: sqlite3.Connection,
    conversion_id: int,
    lcp_ms: float,
    lcp_method: str,
    auto_commit: bool = True,
):
    """Update both lcp_ms and lcp_method columns in a single transaction.

    Args:
        conn: sqlite3.Connection for database access.
        conversion_id: metrics.conversion_id to update.
        lcp_ms: Largest Contentful Paint timing in milliseconds.
        lcp_method: Method or tool used to compute lcp_ms.
        auto_commit: If True, commits the transaction on success.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE metrics SET lcp_ms = ?, lcp_method = ?, updated_at = CURRENT_TIMESTAMP WHERE conversion_id = ?",
            (lcp_ms, lcp_method, conversion_id),
        )
        if auto_commit:
            conn.commit()
    finally:
        cur.close()

def get_pending_metric_tasks(conn: sqlite3.Connection, metric_column: str) -> list:
    """Fetch conversions with NULL value in a specific metric column.

    Finds successful conversions missing a particular quality metric for
    batch computation.

    Args:
        conn: sqlite3.Connection for database access.
        metric_column: Column name from _METRIC_COLUMNS to check for NULL.

    Returns:
        list[dict] with columns: id (conversion_id), filename, tool, format.

    Raises:
        ValueError: If metric_column is not in _METRIC_COLUMNS.
    """
    if metric_column not in _METRIC_COLUMNS:
        raise ValueError(f"Invalid metric column: '{metric_column}'. Allowed: {_METRIC_COLUMNS}")

    cur = conn.cursor()
    try:
        # Whitelisted column name is safe for f-string
        cur.execute(
            f"""
                SELECT c.id, i.filename, c.tool, c.format
                FROM conversions c
                JOIN images i ON c.image_id = i.id
                JOIN metrics m ON c.id = m.conversion_id
                WHERE c.success = 1 AND m.{metric_column} IS NULL
            """
        )
        return [dict(row) for row in cur.fetchall()]
    finally:
        cur.close()

def get_conversion_metrics(conn: sqlite3.Connection, conversion_id: int) -> dict:
    """Fetch all metric values for a single conversion.

    Args:
        conn: sqlite3.Connection for database access.
        conversion_id: metrics.conversion_id to retrieve.

    Returns:
        dict with columns: ssim, ms_ssim, psnr_db, delta_e, lpips, dists,
        meta_score, lcp_ms, compute_ms. Empty dict if no metrics row found.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT ssim, ms_ssim, psnr_db, delta_e,
                   lpips, dists, meta_score, lcp_ms, compute_ms
            FROM metrics
            WHERE conversion_id = ?
            """,
            (conversion_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else {}
    finally:
        cur.close()
