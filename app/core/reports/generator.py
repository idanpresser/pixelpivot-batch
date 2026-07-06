"""Module for generating self-contained HTML reports for batch conversion runs."""

import os
import json
import math
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple, Optional

from app.core.db.connection import get_connection
from app.core.db.repositories.batch import BatchRepository
from app.core.db.repositories.conversions import _CONVERSIONS_SCHEMA
from app.batch_api.models import Tool

# Default lists of categories, tools, and formats to use when scanning directories without a run_id
DEFAULT_CATEGORIES = ["general", "web", "photo"]
DEFAULT_TOOLS = ["magick", "ffmpeg", "vips", "sharp", "cavif"]
DEFAULT_FORMATS = ["webp", "avif", "jxl"]

def _format_size(size_bytes: int) -> str:
    """Format bytes into a human-readable string."""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = int(math.floor(math.log(size_bytes, 1024)))
    p = math.pow(1024, i)
    s = round(size_bytes / p, 2)
    return f"{s} {units[i]}"

def _format_duration(ms: float) -> str:
    """Format milliseconds into a human-readable string."""
    if ms is None:
        return "N/A"
    if ms < 1000:
        return f"{round(ms)} ms"
    s = ms / 1000.0
    if s < 60:
        return f"{round(s, 2)} s"
    m = int(s // 60)
    rem_s = round(s % 60, 1)
    return f"{m}m {rem_s}s"

def _generate_svg_chart(points: List[Tuple[float, float]], title: str, y_label: str, is_pct: bool = False) -> str:
    """Generate a self-contained inline SVG line chart with gradients and grid lines."""
    if not points:
        return f'<div class="no-chart">No telemetry data recorded for {title}</div>'

    # Filter out invalid points
    valid_points = [(x, y) for x, y in points if x is not None and y is not None]
    if not valid_points:
        return f'<div class="no-chart">No valid telemetry data recorded for {title}</div>'

    # Find boundaries
    times = [p[0] for p in valid_points]
    vals = [p[1] for p in valid_points]
    min_x, max_x = min(times), max(times)
    min_y, max_y = min(vals), max(vals)

    # Pad boundaries
    if max_x == min_x:
        max_x = min_x + 1.0
    if max_y == min_y:
        max_y = min_y + 10.0 if is_pct else min_y + 10.0
    
    if is_pct:
        max_y = min(100.0, max(max_y, 10.0))
        min_y = 0.0
    else:
        min_y = max(0.0, min_y - (max_y - min_y) * 0.1)
        max_y = max_y + (max_y - min_y) * 0.1

    width = 600
    height = 180
    left_m, right_m, top_m, bottom_m = 45, 15, 15, 25
    chart_w = width - left_m - right_m
    chart_h = height - top_m - bottom_m

    # Generate points mapping
    svg_points = []
    for x, y in valid_points:
        px = left_m + ((x - min_x) / (max_x - min_x)) * chart_w
        py = top_m + chart_h - ((y - min_y) / (max_y - min_y)) * chart_h
        svg_points.append((px, py))

    # Path data
    line_path = " ".join([f"{'M' if i == 0 else 'L'} {px:.1f} {py:.1f}" for i, (px, py) in enumerate(svg_points)])
    
    # Area path (closed at the bottom)
    area_path = f"{line_path} L {svg_points[-1][0]:.1f} {top_m+chart_h:.1f} L {svg_points[0][0]:.1f} {top_m+chart_h:.1f} Z"

    # Y-axis ticks
    y_ticks = []
    for i in range(4):
        val = min_y + (max_y - min_y) * (i / 3.0)
        py = top_m + chart_h - (i / 3.0) * chart_h
        unit = "%" if is_pct else " MB"
        y_ticks.append(f'<text x="{left_m-8}" y="{py+4}" text-anchor="end" class="chart-text">{round(val, 1)}{unit}</text>')
        y_ticks.append(f'<line x1="{left_m}" y1="{py}" x2="{width-right_m}" y2="{py}" class="chart-grid-line" />')

    # X-axis ticks (timestamps)
    x_ticks = []
    num_x_ticks = min(5, len(valid_points))
    for i in range(num_x_ticks):
        idx = int(i * (len(valid_points) - 1) / max(1, num_x_ticks - 1))
        x, _ = valid_points[idx]
        px = svg_points[idx][0]
        # Format as relative elapsed time
        elapsed_s = int(x - min_x)
        min_part = elapsed_s // 60
        sec_part = elapsed_s % 60
        label = f"{min_part}m {sec_part}s" if min_part > 0 else f"{sec_part}s"
        x_ticks.append(f'<text x="{px}" y="{height-5}" text-anchor="middle" class="chart-text">{label}</text>')
        x_ticks.append(f'<line x1="{px}" y1="{top_m}" x2="{px}" y2="{top_m+chart_h}" class="chart-grid-line" />')

    # Color definitions
    color_stroke = "#10b981" if is_pct else "#6366f1"
    color_fill_grad = "url(#grad-cpu)" if is_pct else "url(#grad-ram)"

    svg = f"""
    <svg viewBox="0 0 {width} {height}" class="telemetry-chart">
        <defs>
            <linearGradient id="grad-cpu" x1="0%" y1="0%" x2="0%" y2="100%">
                <stop offset="0%" stop-color="#10b981" stop-opacity="0.3"/>
                <stop offset="100%" stop-color="#10b981" stop-opacity="0.0"/>
            </linearGradient>
            <linearGradient id="grad-ram" x1="0%" y1="0%" x2="0%" y2="100%">
                <stop offset="0%" stop-color="#6366f1" stop-opacity="0.3"/>
                <stop offset="100%" stop-color="#6366f1" stop-opacity="0.0"/>
            </linearGradient>
        </defs>
        <!-- Grid lines -->
        {"".join(y_ticks)}
        {"".join(x_ticks)}
        <!-- Area path -->
        <path d="{area_path}" fill="{color_fill_grad}" />
        <!-- Line path -->
        <path d="{line_path}" fill="none" stroke="{color_stroke}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
        <!-- Y Axis border -->
        <line x1="{left_m}" y1="{top_m}" x2="{left_m}" y2="{top_m+chart_h}" stroke="#475569" stroke-width="1" />
        <!-- X Axis border -->
        <line x1="{left_m}" y1="{top_m+chart_h}" x2="{width-right_m}" y2="{top_m+chart_h}" stroke="#475569" stroke-width="1" />
    </svg>
    """
    return svg

def _parse_categories_tools_formats(tool_str: str, format_str: str, cat_str: str) -> Tuple[List[str], List[str], List[str]]:
    """Parse comma-separated or JSON list strings into list of tools, formats, and categories."""
    def clean(s: str) -> List[str]:
        if not s:
            return []
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                return [str(x).strip() for x in parsed]
            except Exception:
                pass
        return [x.strip() for x in s.split(",") if x.strip()]

    return clean(tool_str), clean(format_str), clean(cat_str)

def _scan_files_and_query_db(
    conn,
    source_dir: str,
    target_dir: str,
    tools: List[str],
    formats: List[str],
    categories: List[str],
    run_id: Optional[int] = None
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Scan filesystem directories and match files against DB conversion records."""
    src_path = Path(source_dir)
    tgt_path = Path(target_dir)

    if not src_path.exists():
        return [], [{"path": "N/A", "error": f"Source directory does not exist: {source_dir}"}]

    # Inventory all source files
    src_files = []
    source_stems = {}
    valid_exts = {".png", ".jpg", ".jpeg", ".webp", ".avif", ".jxl", ".heic", ".heif", ".tiff", ".bmp"}
    for item in src_path.glob("**/*"):
        if item.is_file() and item.suffix.lower() in valid_exts:
            src_files.append(item)
            source_stems[item.stem] = item

    # Inventory all target files
    tgt_files = {}
    if tgt_path.exists():
        for item in tgt_path.glob("**/*"):
            if item.is_file() and item.suffix.lower() in {".webp", ".avif", ".jxl", ".png", ".jpeg"}:
                tgt_files[item.name] = item

    # Get DB registered conversions for these source files
    db_conversions = {}
    if source_stems:
        filenames = list(source_stems.keys())
        # We also need suffixes or extensions for absolute match. 
        # But we query all matching filenames in the DB conversions image set
        placeholders = ",".join(["?"] * len(filenames))
        query = f"""
            SELECT c.id, c.image_id, c.format, c.tool, c.quality, c.parameters, c.duration_ms,
                   c.cpu_avg_pct, c.cpu_peak_pct, c.ram_peak_mb, c.output_size_bytes, c.savings_pct,
                   c.success, c.error_message, c.created_at,
                   i.filename, i.size_bytes as original_size, i.width, i.height, i.is_corrupt,
                   m.ssim, m.psnr_db, m.lpips, m.meta_score
            FROM conversions c
            JOIN images i ON c.image_id = i.id
            LEFT JOIN metrics m ON c.id = m.conversion_id
            WHERE i.filename IN ({placeholders})
        """
        try:
            cur = conn.cursor()
            cur.execute(query, [source_stems[stem].name for stem in filenames])
            rows = cur.fetchall()
            for r in rows:
                row_dict = dict(r)
                # Key format: (filename, tool, format)
                db_conversions[(row_dict["filename"], row_dict["tool"], row_dict["format"])] = row_dict
        except Exception as e:
            # Safe fallthrough if DB query fails or table missing
            pass

    # Reconcile files and build details list
    reconciled_details = []
    errors = []

    # Get explicitly recorded errors for this run (if run_id provided)
    run_db_errors = {}
    if run_id is not None:
        try:
            cur = conn.cursor()
            cur.execute("SELECT input_path, error, is_dlq FROM batch_errors WHERE batch_id = ?", (run_id,))
            for r in cur.fetchall():
                row = dict(r)
                if row["input_path"]:
                    run_db_errors[Path(row["input_path"]).name] = row
                    errors.append({"path": row["input_path"], "error": row["error"], "is_dlq": row["is_dlq"]})
        except Exception:
            pass

    # If run_id is provided, check if multiple categories exist in the batch to decide suffix format
    multi_category = len(categories) > 1

    # Matrix combination iteration
    for stem, src_file in source_stems.items():
        original_size = src_file.stat().st_size
        src_name = src_file.name

        # Check if the whole source file failed upfront (e.g. unreadable/corrupt)
        if src_name in run_db_errors:
            # Entire file failed to process
            continue

        for cat in categories:
            for t in tools:
                for fmt in formats:
                    # Formulate expected output name(s)
                    # Suffix depends on multi-category status
                    expected_suffix = f"_{t}"
                    if multi_category:
                        expected_suffix = f"_{cat}{expected_suffix}"
                    
                    expected_out_name = f"{stem}{expected_suffix}.{fmt}"

                    # Alternate checks for manual directories report where multi_category is unknown
                    alt_out_name = f"{stem}_{cat}_{t}.{fmt}"
                    alt_out_name2 = f"{stem}_{t}.{fmt}"

                    out_file = None
                    out_name_used = ""
                    if expected_out_name in tgt_files:
                        out_file = tgt_files[expected_out_name]
                        out_name_used = expected_out_name
                    elif alt_out_name in tgt_files:
                        out_file = tgt_files[alt_out_name]
                        out_name_used = alt_out_name
                    elif alt_out_name2 in tgt_files:
                        out_file = tgt_files[alt_out_name2]
                        out_name_used = alt_out_name2

                    # Fetch DB details for this combination
                    db_rec = db_conversions.get((src_name, t, fmt))
                    
                    # Status evaluation
                    status = "success"
                    error_msg = None
                    warning_msg = None
                    duration = None
                    quality = None
                    ssim = None
                    output_size = None

                    if db_rec:
                        success_val = db_rec.get("success", False)
                        status = "success" if success_val else "failed"
                        error_msg = db_rec.get("error_message")
                        duration = db_rec.get("duration_ms")
                        quality = db_rec.get("quality")
                        ssim = db_rec.get("ssim")
                        output_size = db_rec.get("output_size_bytes")
                    
                    # File reconciliation checks
                    if out_file:
                        physical_size = out_file.stat().st_size
                        if output_size is None:
                            output_size = physical_size
                        
                        if not db_rec:
                            # Output exists on disk but is missing in DB
                            status = "warning"
                            warning_msg = "Output exists on disk but has no database record."
                        elif not db_rec.get("success"):
                            # Output exists physically but DB marked it failed
                            status = "warning"
                            warning_msg = "Output exists on disk but database records it as a failure."
                        elif abs(physical_size - output_size) > 1024: # > 1KB mismatch
                            warning_msg = f"Size mismatch: DB registers {output_size} B, physical file is {physical_size} B."
                    else:
                        # Output file not found on disk
                        if db_rec and db_rec.get("success"):
                            # DB says success, but physical output is missing
                            status = "warning"
                            warning_msg = "Database marks success, but output file is missing from target directory."
                        elif db_rec and not db_rec.get("success"):
                            # DB says failure, output file is naturally missing
                            status = "failed"
                        else:
                            # No file on disk, no entry in DB - represents an unexecuted combination (e.g. skipped or unsupported format)
                            continue

                    # Calculate savings
                    savings_pct = 0.0
                    if output_size is not None and original_size > 0:
                        savings_pct = (original_size - output_size) / original_size * 100

                    reconciled_details.append({
                        "source_name": src_name,
                        "source_path": str(src_file),
                        "output_name": out_name_used or expected_out_name,
                        "tool": t,
                        "format": fmt,
                        "category": cat,
                        "original_size": original_size,
                        "output_size": output_size or 0,
                        "savings_pct": savings_pct,
                        "duration_ms": duration,
                        "quality": quality,
                        "ssim": ssim,
                        "status": status,
                        "error_message": error_msg,
                        "warning_message": warning_msg,
                    })

                    if status == "failed" and error_msg:
                        errors.append({"path": str(src_file), "error": f"{t}/{fmt}: {error_msg}", "is_dlq": False})

    # Find orphan target files in target_dir that don't match any source stems
    for name, item in tgt_files.items():
        # Parse the stem by stripping suffixes
        # e.g., image1_general_magick.webp -> image1
        matched_stem = None
        for stem in source_stems.keys():
            if name.startswith(stem + "_"):
                matched_stem = stem
                break
        
        if not matched_stem:
            # This is an orphan file
            reconciled_details.append({
                "source_name": "N/A",
                "source_path": "N/A",
                "output_name": name,
                "tool": "unknown",
                "format": item.suffix.lstrip("."),
                "category": "unknown",
                "original_size": 0,
                "output_size": item.stat().st_size,
                "savings_pct": 0.0,
                "duration_ms": None,
                "quality": None,
                "ssim": None,
                "status": "warning",
                "error_message": None,
                "warning_message": "Orphan output: Source image file not found in source directory.",
            })

    return reconciled_details, errors

def _render_html(
    title: str,
    meta_info: Dict[str, Any],
    summary_metrics: Dict[str, Any],
    details: List[Dict[str, Any]],
    errors: List[Dict[str, Any]],
    telemetry_charts_html: str,
) -> str:
    """Render the self-contained premium HTML template."""
    # Convert lists to JSON for vanilla JS search/filter/sort logic
    details_json = json.dumps(details)

    # CSS styles
    css = """
    :root {
        --bg-color: #0f172a;
        --card-bg: #1e293b;
        --card-border: #334155;
        --text-main: #f8fafc;
        --text-secondary: #94a3b8;
        --accent-emerald: #10b981;
        --accent-indigo: #6366f1;
        --accent-rose: #f43f5e;
        --accent-amber: #f59e0b;
        --font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    
    body {
        margin: 0;
        padding: 0;
        font-family: var(--font-family);
        background-color: var(--bg-color);
        color: var(--text-main);
        line-height: 1.5;
    }

    .container {
        max-width: 1200px;
        margin: 0 auto;
        padding: 2rem 1.5rem;
    }

    header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 2rem;
        border-bottom: 1px solid var(--card-border);
        padding-bottom: 1.5rem;
    }

    h1 {
        margin: 0 0 0.5rem 0;
        font-size: 1.85rem;
        font-weight: 700;
        background: linear-gradient(135deg, #a78bfa, #6366f1);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }

    .subtitle {
        color: var(--text-secondary);
        font-size: 0.95rem;
        margin: 0;
    }

    .badge {
        padding: 0.25rem 0.75rem;
        border-radius: 9999px;
        font-size: 0.8rem;
        font-weight: 600;
        text-transform: uppercase;
        display: inline-block;
    }

    .badge-completed { background-color: rgba(16, 185, 129, 0.15); color: var(--accent-emerald); border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-running { background-color: rgba(99, 102, 241, 0.15); color: var(--accent-indigo); border: 1px solid rgba(99, 102, 241, 0.3); }
    .badge-failed { background-color: rgba(244, 63, 94, 0.15); color: var(--accent-rose); border: 1px solid rgba(244, 63, 94, 0.3); }
    .badge-cancelled { background-color: rgba(148, 163, 184, 0.15); color: var(--text-secondary); border: 1px solid rgba(148, 163, 184, 0.3); }

    /* Grid layout */
    .dashboard-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
        gap: 1.5rem;
        margin-bottom: 2.5rem;
    }

    .card {
        background-color: var(--card-bg);
        border: 1px solid var(--card-border);
        border-radius: 12px;
        padding: 1.25rem;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1);
    }

    .card-title {
        color: var(--text-secondary);
        font-size: 0.85rem;
        font-weight: 600;
        text-transform: uppercase;
        margin-bottom: 0.5rem;
        letter-spacing: 0.05em;
    }

    .card-value {
        font-size: 1.75rem;
        font-weight: 700;
        margin: 0;
    }

    .card-meta {
        font-size: 0.8rem;
        color: var(--text-secondary);
        margin-top: 0.25rem;
    }

    /* Metadata details */
    .meta-table {
        width: 100%;
        border-collapse: collapse;
    }

    .meta-table td {
        padding: 0.4rem 0;
        font-size: 0.9rem;
    }

    .meta-table td:first-child {
        color: var(--text-secondary);
        width: 35%;
        font-weight: 500;
    }

    /* Telemetry row */
    .telemetry-row {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
        gap: 1.5rem;
        margin-bottom: 2.5rem;
    }

    .chart-container {
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
    }

    .telemetry-chart {
        width: 100%;
        max-width: 600px;
        height: auto;
    }

    .chart-text {
        font-size: 9px;
        fill: var(--text-secondary);
        font-family: var(--font-family);
    }

    .chart-grid-line {
        stroke: #334155;
        stroke-dasharray: 2, 2;
        stroke-width: 0.5;
    }

    .no-chart {
        height: 150px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--text-secondary);
        font-size: 0.9rem;
        border: 1px dashed var(--card-border);
        border-radius: 8px;
        width: 100%;
    }

    /* Search & Filter bar */
    .controls-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        flex-wrap: wrap;
        gap: 1rem;
        margin-bottom: 1rem;
    }

    .search-input {
        background-color: var(--card-bg);
        border: 1px solid var(--card-border);
        color: var(--text-main);
        padding: 0.5rem 1rem;
        border-radius: 8px;
        font-size: 0.9rem;
        min-width: 250px;
        outline: none;
        transition: border-color 0.2s;
    }

    .search-input:focus {
        border-color: var(--accent-indigo);
    }

    .filter-buttons {
        display: flex;
        gap: 0.5rem;
    }

    .btn {
        background-color: var(--card-bg);
        border: 1px solid var(--card-border);
        color: var(--text-main);
        padding: 0.4rem 0.8rem;
        border-radius: 8px;
        font-size: 0.85rem;
        cursor: pointer;
        font-weight: 500;
        transition: all 0.2s;
    }

    .btn:hover {
        background-color: #334155;
    }

    .btn-active {
        background-color: var(--accent-indigo);
        border-color: var(--accent-indigo);
    }

    /* Reconciled Files Table */
    .table-container {
        overflow-x: auto;
        border: 1px solid var(--card-border);
        border-radius: 12px;
        background-color: var(--card-bg);
        margin-bottom: 2.5rem;
        box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
    }

    .details-table {
        width: 100%;
        border-collapse: collapse;
        text-align: left;
        font-size: 0.85rem;
    }

    .details-table th, .details-table td {
        padding: 0.75rem 1rem;
        border-bottom: 1px solid var(--card-border);
    }

    .details-table th {
        background-color: #1e293b;
        color: var(--text-secondary);
        font-weight: 600;
        cursor: pointer;
        user-select: none;
    }

    .details-table th:hover {
        color: var(--text-main);
        background-color: #334155;
    }

    .details-table tr:hover td {
        background-color: rgba(99, 102, 241, 0.04);
    }

    .cell-status-success { color: var(--accent-emerald); font-weight: 600; }
    .cell-status-failed { color: var(--accent-rose); font-weight: 600; }
    .cell-status-warning { color: var(--accent-amber); font-weight: 600; }

    .warning-box {
        margin-top: 0.25rem;
        padding: 0.2rem 0.5rem;
        background-color: rgba(245, 158, 11, 0.1);
        border: 1px solid rgba(245, 158, 11, 0.2);
        border-radius: 4px;
        font-size: 0.75rem;
        color: var(--accent-amber);
    }

    .error-box {
        margin-top: 0.25rem;
        padding: 0.2rem 0.5rem;
        background-color: rgba(244, 63, 94, 0.1);
        border: 1px solid rgba(244, 63, 94, 0.2);
        border-radius: 4px;
        font-size: 0.75rem;
        color: var(--accent-rose);
    }

    /* Error Logs area */
    .errors-card {
        border-left: 4px solid var(--accent-rose);
    }

    .error-log-item {
        border-bottom: 1px solid var(--card-border);
        padding: 0.75rem 0;
    }

    .error-log-item:last-child {
        border-bottom: none;
    }

    .error-log-path {
        font-family: monospace;
        font-size: 0.85rem;
        font-weight: 600;
        color: var(--text-main);
        word-break: break-all;
    }

    .error-log-msg {
        font-family: monospace;
        font-size: 0.8rem;
        color: var(--accent-rose);
        margin-top: 0.25rem;
        white-space: pre-wrap;
    }

    .badge-dlq {
        background-color: var(--accent-rose);
        color: white;
        font-size: 0.65rem;
        padding: 0.1rem 0.3rem;
        border-radius: 4px;
        margin-left: 0.5rem;
    }
    """

    # Interactive JS script
    js = f"""
    const detailsData = {details_json};
    let currentFilter = 'all';
    let currentSearch = '';
    let sortColumn = 'source_name';
    let sortAsc = true;

    document.addEventListener("DOMContentLoaded", () => {{
        renderTable();
        
        document.getElementById("search-input").addEventListener("input", (e) => {{
            currentSearch = e.target.value.toLowerCase();
            renderTable();
        }});
    }});

    function filterTable(status) {{
        currentFilter = status;
        document.querySelectorAll(".btn-filter").forEach(b => b.classList.remove("btn-active"));
        document.getElementById("btn-filter-" + status).classList.add("btn-active");
        renderTable();
    }}

    function sortTable(column) {{
        if (sortColumn === column) {{
            sortAsc = !sortAsc;
        }} else {{
            sortColumn = column;
            sortAsc = true;
        }}
        renderTable();
    }}

    function renderTable() {{
        const tbody = document.getElementById("table-body");
        tbody.innerHTML = "";

        // 1. Filter
        let filtered = detailsData.filter(row => {{
            const matchesSearch = 
                (row.source_name && row.source_name.toLowerCase().includes(currentSearch)) ||
                (row.output_name && row.output_name.toLowerCase().includes(currentSearch)) ||
                (row.tool && row.tool.toLowerCase().includes(currentSearch)) ||
                (row.format && row.format.toLowerCase().includes(currentSearch));
            
            if (!matchesSearch) return false;
            
            if (currentFilter === 'all') return true;
            return row.status === currentFilter;
        }});

        // 2. Sort
        filtered.sort((a, b) => {{
            let v1 = a[sortColumn];
            let v2 = b[sortColumn];
            
            if (v1 === null || v1 === undefined) v1 = '';
            if (v2 === null || v2 === undefined) v2 = '';

            if (typeof v1 === 'string') {{
                return sortAsc ? v1.localeCompare(v2) : v2.localeCompare(v1);
            }} else {{
                return sortAsc ? (v1 - v2) : (v2 - v1);
            }}
        }});

        // 3. Render
        if (filtered.length === 0) {{
            tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;color:var(--text-secondary);padding:2rem;">No matching files found.</td></tr>`;
            return;
        }}

        filtered.forEach(row => {{
            const tr = document.createElement("tr");
            
            // Format sizes
            const sizeIn = formatBytes(row.original_size);
            const sizeOut = formatBytes(row.output_size);
            const savingsStr = row.original_size > 0 && row.output_size > 0 
                ? (row.savings_pct < 0 ? '+' : '-') + Math.abs(row.savings_pct).toFixed(1) + '%' 
                : 'N/A';
            
            const durStr = row.duration_ms !== null ? formatDuration(row.duration_ms) : 'N/A';
            const qualityStr = row.quality !== null ? row.quality.toFixed(1) : 'N/A';
            const ssimStr = row.ssim !== null ? row.ssim.toFixed(4) : 'N/A';
            
            let statusClass = 'cell-status-success';
            if (row.status === 'failed') statusClass = 'cell-status-failed';
            else if (row.status === 'warning') statusClass = 'cell-status-warning';

            let warnHTML = '';
            if (row.warning_message) {{
                warnHTML = `<div class="warning-box">⚠️ ${{row.warning_message}}</div>`;
            }}
            let errHTML = '';
            if (row.error_message) {{
                errHTML = `<div class="error-box">❌ ${{row.error_message}}</div>`;
            }}

            tr.innerHTML = `
                <td>
                    <strong>${{row.source_name}}</strong>
                    <div style="font-size:0.75rem;color:var(--text-secondary);word-break:break-all;">${{row.source_path}}</div>
                </td>
                <td>${{row.category}}</td>
                <td>${{row.tool}}</td>
                <td>${{row.format}}</td>
                <td>${{sizeIn}}</td>
                <td>${{sizeOut}}</td>
                <td style="font-weight:600;color:${{row.savings_pct > 0 ? 'var(--accent-emerald)' : 'var(--text-main)'}}">${{savingsStr}}</td>
                <td>${{durStr}}</td>
                <td>${{ssimStr}} / ${{qualityStr}}</td>
                <td>
                    <span class="${{statusClass}}">${{row.status}}</span>
                    ${{warnHTML}}
                    ${{errHTML}}
                </td>
            `;
            tbody.appendChild(tr);
        }});
    }}

    function formatBytes(bytes) {{
        if (bytes <= 0) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
    }}

    function formatDuration(ms) {{
        if (ms < 1000) return Math.round(ms) + ' ms';
        const s = ms / 1000;
        return s.toFixed(2) + ' s';
    }}
    """;

    # Render metadata table rows
    meta_rows = ""
    for k, v in meta_info.items():
        meta_rows += f"<tr><td>{k}</td><td>{v}</td></tr>"

    # Render summary metric values
    total_images_in = summary_metrics.get("total_images", 0)
    success_count = summary_metrics.get("success_count", 0)
    failure_count = summary_metrics.get("failure_count", 0)
    savings_pct = summary_metrics.get("savings_pct", 0.0)
    duration_str = _format_duration(summary_metrics.get("duration_ms"))
    
    input_bytes = summary_metrics.get("total_input_bytes", 0)
    output_bytes = summary_metrics.get("total_output_bytes", 0)
    space_saved = max(0, input_bytes - output_bytes)

    # Render status badge
    status_str = meta_info.get("Status", "unknown").lower()
    badge_class = f"badge-{status_str}" if status_str in ["completed", "running", "failed", "cancelled"] else "badge-cancelled"

    # Render Errors Log Section
    errors_section = ""
    if errors:
        error_items = []
        for e in errors:
            dlq_badge = '<span class="badge-dlq">DLQ Quarantine</span>' if e.get("is_dlq") else ""
            error_items.append(f"""
            <div class="error-log-item">
                <div class="error-log-path">{e['path']}{dlq_badge}</div>
                <div class="error-log-msg">{e['error']}</div>
            </div>
            """)
        
        errors_section = f"""
        <div class="card errors-card" style="margin-top: 2rem;">
            <div class="card-title" style="color:var(--accent-rose);">Execution Error Logs ({len(errors)})</div>
            <div style="max-height: 400px; overflow-y: auto; padding-right: 0.5rem;">
                {"".join(error_items)}
            </div>
        </div>
        """

    # Assemble HTML
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        {css}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div>
                <h1>{title}</h1>
                <p class="subtitle">Generated on {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
            </div>
            <div>
                <span class="badge {badge_class}">{meta_info.get("Status", "completed")}</span>
            </div>
        </header>

        <!-- KPI dashboard cards -->
        <div class="dashboard-grid">
            <div class="card">
                <div class="card-title">Space Savings</div>
                <div class="card-value" style="color: var(--accent-emerald);">{savings_pct:+.1f}%</div>
                <div class="card-meta">Saved {_format_size(space_saved)} (In: {_format_size(input_bytes)} / Out: {_format_size(output_bytes)})</div>
            </div>
            <div class="card">
                <div class="card-title">Run Duration</div>
                <div class="card-value">{duration_str}</div>
                <div class="card-meta">Throughput: {summary_metrics.get("yield_mb_sec", 0.0):.2f} MB/s</div>
            </div>
            <div class="card">
                <div class="card-title">Success / Failure</div>
                <div class="card-value">{success_count} <span style="font-size:1.15rem;color:var(--text-secondary);font-weight:normal;">/</span> <span style="color:{'var(--accent-rose)' if failure_count > 0 else 'var(--text-secondary)'}">{failure_count}</span></div>
                <div class="card-meta">Total expected conversions: {total_images_in}</div>
            </div>
            <div class="card">
                <div class="card-title">Run Details</div>
                <table class="meta-table">
                    {meta_rows}
                </table>
            </div>
        </div>

        <!-- Telemetry Graphs -->
        {telemetry_charts_html}

        <!-- Interactive Files List -->
        <div class="controls-row">
            <h2 style="margin: 0; font-size: 1.25rem;">File-by-File Reconciliation</h2>
            <div style="display: flex; gap: 1rem; flex-wrap: wrap;">
                <input type="text" id="search-input" class="search-input" placeholder="Search files, tools, formats...">
                <div class="filter-buttons">
                    <button id="btn-filter-all" class="btn btn-filter btn-active" onclick="filterTable('all')">All</button>
                    <button id="btn-filter-success" class="btn btn-filter" onclick="filterTable('success')">Success</button>
                    <button id="btn-filter-failed" class="btn btn-filter" onclick="filterTable('failed')">Failed</button>
                    <button id="btn-filter-warning" class="btn btn-filter" onclick="filterTable('warning')">Warnings</button>
                </div>
            </div>
        </div>

        <div class="table-container">
            <table class="details-table">
                <thead>
                    <tr>
                        <th onclick="sortTable('source_name')">Source Image</th>
                        <th onclick="sortTable('category')">Category</th>
                        <th onclick="sortTable('tool')">Tool</th>
                        <th onclick="sortTable('format')">Format</th>
                        <th onclick="sortTable('original_size')">Original Size</th>
                        <th onclick="sortTable('output_size')">Output Size</th>
                        <th onclick="sortTable('savings_pct')">Savings</th>
                        <th onclick="sortTable('duration_ms')">Duration</th>
                        <th onclick="sortTable('ssim')">SSIM / Quality</th>
                        <th onclick="sortTable('status')">Status</th>
                    </tr>
                </thead>
                <tbody id="table-body">
                    <!-- Dynamic Rows Insertion -->
                </tbody>
            </table>
        </div>

        <!-- Error Logs Section -->
        {errors_section}
    </div>

    <script>
        {js}
    </script>
</body>
</html>
"""
    return html

def generate_report_for_run(run_id: int, output_path: str) -> None:
    """Generate HTML conversion report for a specific batch run_id."""
    with get_connection() as conn:
        repo = BatchRepository()
        run = repo.get_run(conn, run_id)
        if not run:
            raise ValueError(f"Batch run ID {run_id} not found in database.")

        summary = repo.get_summary(conn, run_id) or {}
        
        # Parse configurations
        tools, formats, categories = _parse_categories_tools_formats(
            run.get("tool", ""), run.get("target_format", ""), run.get("category", "")
        )

        # Retrieve file items and errors
        details, errors = _scan_files_and_query_db(
            conn=conn,
            source_dir=run["source_dir"],
            target_dir=run["target_dir"],
            tools=tools,
            formats=formats,
            categories=categories,
            run_id=run_id
        )

        # Calculate bytes sizes of processed files
        total_input_bytes = sum(d["original_size"] for d in details if d["status"] != "failed")
        total_output_bytes = sum(d["output_size"] for d in details if d["status"] == "success" or d["status"] == "warning")

        # Telemetry graphs
        telemetry_charts_html = ""
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT timestamp, cpu_pct, ram_mb 
                FROM batch_telemetry 
                WHERE run_id = ? 
                ORDER BY timestamp ASC
            """, (run_id,))
            samples = cur.fetchall()
            
            if samples:
                start_dt = None
                cpu_points = []
                ram_points = []
                for s in samples:
                    ts = s["timestamp"]
                    if isinstance(ts, str):
                        # Convert SQLite datetime string
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        except Exception:
                            dt = datetime.strptime(ts.split(".")[0], "%Y-%m-%d %H:%M:%S")
                    else:
                        dt = ts
                    
                    if start_dt is None:
                        start_dt = dt
                    
                    elapsed_s = (dt - start_dt).total_seconds()
                    cpu_points.append((elapsed_s, s["cpu_pct"]))
                    ram_points.append((elapsed_s, s["ram_mb"]))

                cpu_svg = _generate_svg_chart(cpu_points, "CPU Utilization", "CPU", is_pct=True)
                ram_svg = _generate_svg_chart(ram_points, "RAM Utilization", "RAM")

                telemetry_charts_html = f"""
                <div class="telemetry-row">
                    <div class="card chart-container">
                        <div class="card-title">CPU Utilization (Time series)</div>
                        {cpu_svg}
                    </div>
                    <div class="card chart-container">
                        <div class="card-title">RAM Utilization (Time series)</div>
                        {ram_svg}
                    </div>
                </div>
                """
        except Exception as tel_err:
            # Safe fallthrough
            telemetry_charts_html = f'<div class="no-chart">Could not retrieve telemetry charts: {tel_err}</div>'

    # Build report info
    meta_info = {
        "Run ID": f"#{run_id}",
        "Source Folder": run["source_dir"],
        "Target Folder": run["target_dir"],
        "Tools Configuration": ", ".join(tools),
        "Formats Configuration": ", ".join(formats),
        "Trigger Method": run["trigger_type"],
        "Status": run["status"].upper(),
        "Created At": run["created_at"],
    }

    summary_metrics = {
        "total_images": run.get("total_images", len(details)),
        "success_count": summary.get("success_count", len([d for d in details if d["status"] == "success"])),
        "failure_count": summary.get("failure_count", len([d for d in details if d["status"] == "failed"])),
        "savings_pct": summary.get("savings_pct", 0.0),
        "duration_ms": summary.get("duration_ms"),
        "yield_mb_sec": summary.get("yield_mb_sec", 0.0),
        "total_input_bytes": total_input_bytes,
        "total_output_bytes": total_output_bytes,
    }

    # If duration exists in summary, use it. Otherwise compute from database timestamps
    if not summary_metrics["duration_ms"] and run.get("completed_at"):
        try:
            # Parse timestamps
            def parse_ts(t_str):
                return datetime.fromisoformat(t_str.replace("Z", "+00:00"))
            dur = (parse_ts(run["completed_at"]) - parse_ts(run["created_at"])).total_seconds() * 1000
            summary_metrics["duration_ms"] = dur
        except Exception:
            pass

    # If savings_pct is missing, calculate manually
    if not summary_metrics["savings_pct"] and total_input_bytes > 0:
        summary_metrics["savings_pct"] = (total_input_bytes - total_output_bytes) / total_input_bytes * 100

    html = _render_html(
        title=f"Batch Conversion Report - Run #{run_id}",
        meta_info=meta_info,
        summary_metrics=summary_metrics,
        details=details,
        errors=errors,
        telemetry_charts_html=telemetry_charts_html,
    )

    # Write report
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

def generate_report_for_dirs(source_dir: str, target_dir: str, output_path: str) -> None:
    """Generate HTML conversion summary based entirely on files in source/target dirs and matching DB entries."""
    # Resolve absolute paths
    from app.batch_api.models import _resolve_path
    abs_src = _resolve_path(source_dir)
    abs_tgt = _resolve_path(target_dir)

    with get_connection() as conn:
        # Reconcile files by matching default categories, tools, and formats
        details, errors = _scan_files_and_query_db(
            conn=conn,
            source_dir=abs_src,
            target_dir=abs_tgt,
            tools=DEFAULT_TOOLS,
            formats=DEFAULT_FORMATS,
            categories=DEFAULT_CATEGORIES
        )

    # Calculate metrics
    success_details = [d for d in details if d["status"] == "success" or d["status"] == "warning"]
    failed_details = [d for d in details if d["status"] == "failed"]
    total_input_bytes = sum(d["original_size"] for d in details)
    total_output_bytes = sum(d["output_size"] for d in details)
    
    savings_pct = 0.0
    if total_input_bytes > 0:
        savings_pct = (total_input_bytes - total_output_bytes) / total_input_bytes * 100

    meta_info = {
        "Source Folder": abs_src,
        "Target Folder": abs_tgt,
        "Total Source Images Found": len(set(d["source_name"] for d in details if d["source_name"] != "N/A")),
        "Total Target Images Found": len(details),
        "Status": "COMPLETED" if len(failed_details) == 0 else "WARNINGS",
    }

    summary_metrics = {
        "total_images": len(details),
        "success_count": len(success_details),
        "failure_count": len(failed_details),
        "savings_pct": savings_pct,
        "duration_ms": None,
        "yield_mb_sec": 0.0,
        "total_input_bytes": total_input_bytes,
        "total_output_bytes": total_output_bytes,
    }

    # Folder-wide reports do not have run-bound telemetry logs
    telemetry_charts_html = """
    <div class="card" style="margin-bottom: 2.5rem; text-align: center; color: var(--text-secondary); padding: 1.5rem;">
        Resource utilization telemetry is not available for directory-wide summary reports. 
        Resource charts are only recorded for single batch runs.
    </div>
    """

    html = _render_html(
        title="Folder-wide Conversion Summary Report",
        meta_info=meta_info,
        summary_metrics=summary_metrics,
        details=details,
        errors=errors,
        telemetry_charts_html=telemetry_charts_html,
    )

    # Write report
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
