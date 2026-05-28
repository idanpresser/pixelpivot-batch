"""Analytics data export from conversions, images, and metrics tables.

Functions to retrieve legacy analytics (Phase 1 conversion results) as pandas
DataFrames for dashboard display and quality prior analysis.
"""

import pandas as pd
from .connection import get_connection

def get_dashboard_dataframe(conn=None):
    """Return all conversion results as a pandas DataFrame.

    Args:
        conn: Optional sqlite3.Connection. If None, opens a new connection.

    Returns:
        pandas.DataFrame with columns: id, format, tool, quality, duration_ms,
        cpu_avg_pct, cpu_peak_pct, ram_peak_mb, gpu_peak_pct, vram_peak_mb,
        output_size_bytes, savings_pct, calib_ssim, calib_method, created_at,
        success, error_message, ssim, ms_ssim, psnr_db, lpips, dists, delta_e,
        meta_score, lcp_ms, lcp_method, compute_ms, filename, category,
        resolution, size_bytes, width, height, arrival_time, img_id.
    """
    
    query = """
        SELECT
            c.id, c.format, c.tool, c.quality, c.duration_ms,
            c.cpu_avg_pct, c.cpu_peak_pct, c.ram_peak_mb, c.gpu_peak_pct, c.vram_peak_mb,
            c.output_size_bytes, c.savings_pct,
            c.calib_ssim, c.calib_method, c.created_at, c.success, c.error_message,
            m.ssim, m.ms_ssim, m.psnr_db, m.lpips, m.dists,
            m.delta_e, m.meta_score, m.lcp_ms, m.lcp_method, m.compute_ms,
            i.filename, i.category, 
            i.width || 'x' || i.height AS resolution,
            i.size_bytes, i.width, i.height,
            i.arrival_time, i.id as img_id
        FROM conversions c
        JOIN images i ON c.image_id = i.id
        LEFT JOIN metrics m ON c.id = m.conversion_id
    """

    if conn:
        return pd.read_sql_query(query, conn)
    
    with get_connection() as connection:
        return pd.read_sql_query(query, connection)

def get_benchmark_dataframe():
    """Return all conversion results as a pandas DataFrame.

    Alias for get_dashboard_dataframe(). Returns the same data structure.

    Returns:
        pandas.DataFrame with all conversion results (see get_dashboard_dataframe).
    """
    return get_dashboard_dataframe()

def get_quality_priors_dataframe():
    """Return the quality_priors table as a pandas DataFrame.

    Returns:
        pandas.DataFrame with columns: category, format, tool, mean_quality,
        avg_slope, sample_count. Sorted by category, format, tool.
    """
    query = """
        SELECT category, format, tool, mean_quality, avg_slope, sample_count
        FROM quality_priors
        ORDER BY category, format, tool
    """
    with get_connection() as connection:
        return pd.read_sql_query(query, connection)
