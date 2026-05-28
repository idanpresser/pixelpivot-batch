"""Repository for quality_priors table persistence.

Manages quality hints and statistics per (category, format, tool) triplet
for the heuristic interpolator feedback loop.
"""

import sqlite3
from typing import Optional

def get_quality_prior(conn: sqlite3.Connection, category: str, tool: str, fmt: str, current_bpp: Optional[float] = None) -> Optional[dict]:
    """Fetch quality prior statistics for a (category, format, tool) triplet.

    Args:
        conn: sqlite3.Connection for database access.
        category: Image category (e.g. 'photo', 'screenshot').
        tool: Converter tool name (e.g. 'magick', 'ffmpeg').
        fmt: Image format (e.g. 'webp', 'avif').
        current_bpp: Unused parameter (kept for API compatibility).

    Returns:
        dict with columns: mean_quality, avg_bpp, avg_slope, sample_count;
        or None if no prior exists.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT mean_quality, avg_bpp, avg_slope, sample_count
            FROM quality_priors
            WHERE category = ? AND format = ? AND tool = ?
            """,
            (category, fmt, tool),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    finally:
        cur.close()

def update_quality_prior(
    conn: sqlite3.Connection,
    category: str,
    format: str,
    tool: str,
    mean_quality: float,
    avg_bpp: float = 0.0,
    avg_slope: float = 0.0,
    sample_count: int = 1,
    auto_commit: bool = True
):
    """Insert or update a quality_priors row.

    Uses SQLite UPSERT on conflict (category, format, tool) to allow
    re-runs and quality feedback loop updates.

    Args:
        conn: sqlite3.Connection for database access.
        category: Image category identifier.
        format: Target image format.
        tool: Converter tool name.
        mean_quality: Mean quality value for this (category, format, tool).
        avg_bpp: Average bits-per-pixel (optional).
        avg_slope: Average log-linear slope (optional).
        sample_count: Number of samples in this prior (default 1).
        auto_commit: If True, commits the transaction on success.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO quality_priors (category, format, tool, mean_quality, avg_bpp, avg_slope, sample_count)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (category, format, tool) DO UPDATE SET
                mean_quality = EXCLUDED.mean_quality,
                avg_bpp = EXCLUDED.avg_bpp,
                avg_slope = EXCLUDED.avg_slope,
                sample_count = EXCLUDED.sample_count
            """,
            (category, format, tool, mean_quality, avg_bpp, avg_slope, sample_count)
        )
        if auto_commit:
            conn.commit()
    finally:
        cur.close()
