"""Repository for images table persistence and metadata extraction.

Extracts image dimensions, format, and SHA-256 checksums. Handles corruption
detection and upserts images (by filename+category) to the images table.
"""

import hashlib
import sqlite3
from pathlib import Path
from typing import Optional
from PIL import Image as PILImage

from ...logger import get_logger

log = get_logger(__name__)

def register_image(
    conn: sqlite3.Connection,
    filepath: str,
    category: str,
    arrival_time: str = None,
    image_uuid: str = None,
) -> int:
    """Insert or update an images row with metadata extracted from the file.

    Opens the file with PIL to extract width, height, and format. Computes
    SHA-256 hash of the file content. On PIL failure, marks the row as corrupt.
    Uses SQLite UPSERT on conflict (filename, category).

    Args:
        conn: sqlite3.Connection for database access.
        filepath: Path to the image file.
        category: Category identifier for grouping (e.g. 'screenshots').
        arrival_time: Optional ISO timestamp (or "HH:MM" short form, expanded
            to "1970-01-01 HH:MM:00").
        image_uuid: Optional UUID for the image.

    Returns:
        int: The images.id of the inserted/updated row.
    """
    
    if arrival_time and len(str(arrival_time)) == 5 and ":" in arrival_time:
        # Expand "05:46" to a valid ISO timestamp
        arrival_time = f"1970-01-01 {arrival_time}:00"

    path = Path(filepath)
    filename = path.name
    size = path.stat().st_size
    
    is_corrupt = False
    width, height = 0, 0
    fmt = path.suffix.lstrip(".").lower()
    
    try:
        with PILImage.open(filepath) as img:
            width, height = img.size
            fmt = img.format.lower() if img.format else fmt
    except Exception as e:
        log.warning(f"Metadata extraction failed for {filename}: {e}")
        is_corrupt = True

    sha256_hash_str = "CORRUPT_OR_SKIPPED"
    if not is_corrupt:
        sha256_hash = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256_hash.update(chunk)
        sha256_hash_str = sha256_hash.hexdigest()

    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO images (
                filename, category, arrival_time, image_uuid,
                width, height, size_bytes, format, sha256, is_corrupt
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (filename, category) DO UPDATE 
            SET arrival_time = EXCLUDED.arrival_time,
                image_uuid = EXCLUDED.image_uuid,
                width = EXCLUDED.width,
                height = EXCLUDED.height,
                size_bytes = EXCLUDED.size_bytes,
                format = EXCLUDED.format,
                sha256 = EXCLUDED.sha256,
                is_corrupt = EXCLUDED.is_corrupt
            RETURNING id
            """,
            (filename, category, arrival_time, image_uuid, width, height, size, fmt, sha256_hash_str, is_corrupt),
        )
        row = cur.fetchone()
        return int(row["id"]) if row else 0
    finally:
        cur.close()

def get_image_by_id(conn: sqlite3.Connection, image_id: int) -> Optional[dict]:
    """Fetch a single images row by id.

    Args:
        conn: sqlite3.Connection for database access.
        image_id: images.id to retrieve.

    Returns:
        dict with images columns, or None if not found.
    """
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM images WHERE id = ?", (image_id,))
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()
