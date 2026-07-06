"""Repository for conversions table persistence and metrics creation.

Handles insertion of Phase 1 conversion results (both single and batch),
upserts the conversions table with conflict resolution, and creates/updates
associated metrics rows on success.
"""

import sqlite3
from ..connection import DBConnection
from datetime import datetime, timezone
from typing import List, Optional

_CONVERSIONS_SCHEMA = {
    "image_id": int,
    "format": str,
    "tool": str,
    "parameters": str,
    "quality": float,
    "duration_ms": float,
    "cpu_avg_pct": float,
    "cpu_peak_pct": float,
    "ram_peak_mb": float,
    "gpu_peak_pct": float,
    "vram_peak_mb": float,
    "output_size_bytes": int,
    "savings_pct": float,
    "calib_ssim": float,
    "calib_method": str,
    "error_message": str,
}

def insert_conversion(
    conn: DBConnection, data: dict, auto_commit: bool = True
) -> int:
    """Insert or update a conversions row and create a metrics row on success.

    Validates and coerces input data to the conversions schema. Uses SQLite
    UPSERT on conflict (image_id, format, tool) to allow re-runs. On success,
    creates an associated metrics row if not already present.

    Args:
        conn: DBConnection for database access.
        data: dict with conversion fields. Keys not in _CONVERSIONS_SCHEMA are
            ignored. All values are coerced to their schema type.
        auto_commit: If True, commits the transaction on success.

    Returns:
        int: The conversions.id of the inserted/updated row.
    """
    payload = {}
    for col, coerce in _CONVERSIONS_SCHEMA.items():
        val = data.get(col)
        payload[col] = coerce(val) if val is not None else None

    payload["success"] = bool(data.get("success", False))
    payload["created_at"] = data.get("created_at", datetime.now(timezone.utc))

    cur = conn.cursor()
    try:
        # SQLite UPSERT: Handle conflict on (image_id, format, tool)
        cur.execute(
            """
            INSERT INTO conversions (
                image_id, format, tool, quality, parameters,
                duration_ms, cpu_avg_pct, cpu_peak_pct, ram_peak_mb, gpu_peak_pct, vram_peak_mb,
                output_size_bytes, savings_pct, calib_ssim, calib_method,
                success, error_message, created_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?
            )
            ON CONFLICT (image_id, format, tool) DO UPDATE SET
                quality = EXCLUDED.quality,
                parameters = EXCLUDED.parameters,
                duration_ms = EXCLUDED.duration_ms,
                cpu_avg_pct = EXCLUDED.cpu_avg_pct,
                cpu_peak_pct = EXCLUDED.cpu_peak_pct,
                ram_peak_mb = EXCLUDED.ram_peak_mb,
                gpu_peak_pct = EXCLUDED.gpu_peak_pct,
                vram_peak_mb = EXCLUDED.vram_peak_mb,
                output_size_bytes = EXCLUDED.output_size_bytes,
                savings_pct = EXCLUDED.savings_pct,
                calib_ssim = EXCLUDED.calib_ssim,
                calib_method = EXCLUDED.calib_method,
                success = EXCLUDED.success,
                error_message = EXCLUDED.error_message,
                created_at = EXCLUDED.created_at
            RETURNING id
            """,
            (
                payload["image_id"], payload["format"], payload["tool"], payload["quality"], payload["parameters"],
                payload["duration_ms"], payload["cpu_avg_pct"], payload["cpu_peak_pct"], payload["ram_peak_mb"], payload["gpu_peak_pct"], payload["vram_peak_mb"],
                payload["output_size_bytes"], payload["savings_pct"], payload["calib_ssim"], payload["calib_method"],
                payload["success"], payload["error_message"], payload["created_at"]
            ),
        )
        row = cur.fetchone()
        conversion_id = row["id"]
        
        if payload["success"]:
            cur.execute(
                """
                INSERT INTO metrics (conversion_id, updated_at) 
                VALUES (?, CURRENT_TIMESTAMP) 
                ON CONFLICT (conversion_id) DO NOTHING
                """,
                (conversion_id,),
            )
            
        if auto_commit:
            conn.commit()

        return conversion_id
    finally:
        cur.close()

def insert_conversions_batch(
    conn: DBConnection, records: List[dict], auto_commit: bool = True
) -> int:
    """Insert or update multiple conversions rows and create metrics rows.

    Batches schema validation and coercion. Uses SQLite UPSERT on conflict
    (image_id, format, tool). Creates metrics rows for successful conversions.

    Args:
        conn: DBConnection for database access.
        records: list[dict] with conversion fields (see insert_conversion).
        auto_commit: If True, commits the transaction on success.

    Returns:
        int: The number of rows inserted/updated.
    """
    if not records:
        return 0

    payloads = []
    for data in records:
        payload = []
        for col, coerce in _CONVERSIONS_SCHEMA.items():
            val = data.get(col)
            payload.append(coerce(val) if val is not None else None)
        
        payload.append(bool(data.get("success", False)))
        payload.append(data.get("created_at", datetime.now(timezone.utc)))
        payloads.append(tuple(payload))

    cur = conn.cursor()
    try:
        # For batch operations in SQLite, we use a similar UPSERT pattern
        insert_sql = """
            INSERT INTO conversions (
                image_id, format, tool, quality, parameters,
                duration_ms, cpu_avg_pct, cpu_peak_pct, ram_peak_mb, gpu_peak_pct, vram_peak_mb,
                output_size_bytes, savings_pct, calib_ssim, calib_method,
                success, error_message, created_at
            ) VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?
            )
            ON CONFLICT (image_id, format, tool) DO UPDATE SET
                quality = EXCLUDED.quality,
                parameters = EXCLUDED.parameters,
                duration_ms = EXCLUDED.duration_ms,
                cpu_avg_pct = EXCLUDED.cpu_avg_pct,
                cpu_peak_pct = EXCLUDED.cpu_peak_pct,
                ram_peak_mb = EXCLUDED.ram_peak_mb,
                gpu_peak_pct = EXCLUDED.gpu_peak_pct,
                vram_peak_mb = EXCLUDED.vram_peak_mb,
                output_size_bytes = EXCLUDED.output_size_bytes,
                savings_pct = EXCLUDED.savings_pct,
                calib_ssim = EXCLUDED.calib_ssim,
                calib_method = EXCLUDED.calib_method,
                success = EXCLUDED.success,
                error_message = EXCLUDED.error_message,
                created_at = EXCLUDED.created_at
        """
        cur.executemany(insert_sql, payloads)

        # Handle metrics for successful conversions
        cur.execute("""
            INSERT INTO metrics (conversion_id, updated_at)
            SELECT id, CURRENT_TIMESTAMP FROM conversions
            WHERE success = 1
            ON CONFLICT (conversion_id) DO NOTHING
        """)

        if auto_commit:
            conn.commit()
    finally:
        cur.close()
    
    return len(payloads)

def record_conversions(
    conn: DBConnection, records: List[dict], auto_commit: bool = False
) -> int:
    """Persist per-conversion analytics to populate heuristic feedback loop.

    For each record, registers or upserts the image (by path+category), then
    upserts the conversion (by image_id+format+tool). Closes the feedback
    loop so heuristic generators have live conversion data.

    Args:
        conn: DBConnection for database access.
        records: list[dict], each with keys: path (str), category (str),
            format (str), tool (str), quality (float), success (bool).
        auto_commit: If True, commits the transaction on completion.

    Returns:
        int: The number of conversion rows written.
    """
    from .images import register_image

    image_ids: dict = {}
    written = 0
    for r in records:
        key = (r["path"], r["category"])
        image_id = image_ids.get(key)
        if image_id is None:
            image_id = register_image(conn, r["path"], r["category"])
            image_ids[key] = image_id
        insert_conversion(
            conn,
            {
                "image_id": image_id,
                "format": r["format"],
                "tool": r["tool"],
                "quality": r.get("quality"),
                "success": r.get("success", False),
            },
            auto_commit=False,
        )
        written += 1

    if auto_commit:
        conn.commit()
    return written


def get_existing_conversion(conn: DBConnection, image_id: int, tool: str, fmt: str):
    """Fetch the most recent conversions row for (image_id, tool, format).

    Args:
        conn: DBConnection for database access.
        image_id: images.id to filter by.
        tool: conversions.tool to filter by.
        fmt: conversions.format to filter by.

    Returns:
        dict with columns: id, savings_pct, duration_ms, quality, success;
        or None if no matching row exists.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT id, savings_pct, duration_ms, quality, success
               FROM conversions
               WHERE image_id = ? AND tool = ? AND format = ?
               ORDER BY created_at DESC LIMIT 1""",
            (image_id, tool, fmt),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()

def is_benchmarked(conn: DBConnection, image_id: int, tool: str, fmt: str) -> bool:
    """Return True if a conversion exists for (image_id, tool, format).

    Args:
        conn: DBConnection for database access.
        image_id: images.id to check.
        tool: conversions.tool to check.
        fmt: conversions.format to check.

    Returns:
        bool: True if get_existing_conversion returned a row, False otherwise.
    """
    return get_existing_conversion(conn, image_id, tool, fmt) is not None

def remove_failed_images(conn: DBConnection) -> int:
    """Delete all conversions rows where success=0.

    Args:
        conn: DBConnection for database access.

    Returns:
        int: The number of conversions rows deleted.
    """
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM conversions WHERE success = 0")
        count = cur.rowcount
        conn.commit()
        return count
    finally:
        cur.close()

def get_conversion_count(conn: DBConnection) -> int:
    """Return the total number of successful conversions in the database.

    Args:
        conn: DBConnection for database access.

    Returns:
        int: Count of conversions rows where success=1.
    """
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) as total FROM conversions WHERE success = 1")
        row = cur.fetchone()
        return row["total"] if row else 0
    finally:
        cur.close()