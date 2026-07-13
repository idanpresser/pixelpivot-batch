"""Heuristic Table Generator — fits log-linear quality curves from conversion analytics.

Generates the heuristic_table.json used by HeuristicInterpolator. Queries successful
conversions from the database, groups by (category, format, tool), and fits a
least-squares log-linear curve (quality = a + b*log10(megapixels)) over the exact
pixel counts observed during each batch run. Only emits curves with sufficient samples
(HEURISTIC_MIN_SAMPLES); thinner cells are dropped so the interpolator falls back to
tool/format-native defaults.
"""

import json
import math
import numpy as np
import pandas as pd
from collections import defaultdict
from pathlib import Path

from .logger import get_logger
from .paths import APP_ROOT, resolve_data_dir
from .config import HEURISTIC_TABLE_PATH, HEURISTIC_TABLE_VERSION, HEURISTIC_MIN_SAMPLES
from .db import get_connection

log = get_logger(__name__)

# Write regenerated table and weights to the data directory so they persist across upgrades
OUTPUT_TABLE_PATH = resolve_data_dir() / "heuristic_table.json"
OUTPUT_WEIGHTS_PATH = resolve_data_dir() / "heuristic_weights.json"


def fit_log_linear(megapixels, qualities) -> tuple[float, float]:
    """Fit a log-linear curve (quality = a + b*log10(megapixels)) via least squares.

    Quality-vs-resolution is non-linear (diminishing), so a log-linear model
    captures it with two interpretable parameters: intercept (a) and slope (b).
    Degenerate input (fewer than two distinct megapixel values) cannot define a slope,
    so it yields a flat curve at the mean quality (b = 0).

    Args:
        megapixels: List of image megapixel counts.
        qualities: List of corresponding encoder qualities.

    Returns:
        (a, b) tuple defining the curve quality = a + b*log10(megapixels).
    """
    xs = [math.log10(mp) for mp in megapixels]
    if len(set(xs)) < 2:
        return float(sum(qualities) / len(qualities)), 0.0
    b, a = np.polyfit(xs, qualities, 1)
    return float(a), float(b)


def generate_heuristic_table(conn=None, table_path=None, weights_path=None, version=None) -> dict:
    """Generate heuristic_table.json and heuristic_weights.json from conversion analytics.

    Queries successful conversions from the database, groups by (category, format, tool),
    and fits a log-linear curve over exact pixel counts. Only emits curves with at least
    HEURISTIC_MIN_SAMPLES points; thinner cells are dropped. Version is stamped into the
    table so provenance survives regeneration.

    Args:
        conn: Optional open DB connection (else a managed connection is used).
        table_path: Output path for heuristic_table.json (default: canonical location).
        weights_path: Output path for heuristic_weights.json (default: sibling of table).
        version: Version string for the table (default: HEURISTIC_TABLE_VERSION).

    Returns:
        Dict with "heuristic_table" and "heuristic_weights" keys mapping to file paths.
    """
    table_path = Path(table_path) if table_path is not None else OUTPUT_TABLE_PATH
    weights_path = Path(weights_path) if weights_path is not None else OUTPUT_WEIGHTS_PATH
    version = version if version is not None else HEURISTIC_TABLE_VERSION

    query = """
    SELECT
        i.category,
        i.width,
        i.height,
        c.format,
        c.tool,
        c.quality
    FROM conversions c
    JOIN images i ON c.image_id = i.id
    WHERE c.success = 1 AND c.quality IS NOT NULL
    """

    log.info("Fetching data from SQLite for heuristic generation...")
    try:
        if conn is not None:
            df = pd.read_sql_query(query, conn)
        else:
            with get_connection() as _conn:
                df = pd.read_sql_query(query, _conn)
    except Exception as e:
        log.error(f"Failed to fetch data from database: {e}")
        raise

    if df.empty:
        log.error("No successful conversions found in the database. Aborting.")
        raise RuntimeError("No successful conversions found in the database.")

    # Collect raw (megapixels, quality) pairs per (category, format, tool) so a
    # curve can be fitted over the full resolution spread, not 4 bucket means.
    curve_data = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    processed_count = 0

    for _, row in df.iterrows():
        w = row["width"]
        h = row["height"]
        if not w or not h or w <= 0 or h <= 0:
            continue
        mp = (w * h) / 1_000_000.0
        if mp <= 0:
            continue
        curve_data[row["category"]][row["format"]][row["tool"]].append(
            (mp, row["quality"])
        )
        processed_count += 1

    final_dict = {}
    diagnostics = {}
    for cat, fmt_dicts in curve_data.items():
        for fmt, tool_dicts in fmt_dicts.items():
            for tool, pairs in tool_dicts.items():
                n = len(pairs)
                # Gate: a curve needs enough points; thinner cells are dropped so
                # the interpolator falls back to a tool/format-native default.
                if n < HEURISTIC_MIN_SAMPLES:
                    continue
                mps = [p[0] for p in pairs]
                qs = [p[1] for p in pairs]
                a, b = fit_log_linear(mps, qs)
                cell = {
                    "a": round(a, 4),
                    "b": round(b, 4),
                    "n": n,
                    "mp_min": round(min(mps), 4),
                    "mp_max": round(max(mps), 4),
                }
                final_dict.setdefault(cat, {}).setdefault(fmt, {})[tool] = cell
                diagnostics[f"{cat}|{fmt}|{tool}"] = cell

    final_dict["version"] = version

    table_path.parent.mkdir(parents=True, exist_ok=True)
    with open(table_path, "w", encoding="utf-8") as f:
        json.dump(final_dict, f, indent=4)

    weights_payload = {
        "description": "PixelPivot heuristic quality curves (quality = a + b*log10(MP)).",
        "generated_from": "SQLite",
        "lookup": diagnostics,
    }

    with open(weights_path, "w", encoding="utf-8") as f:
        json.dump(weights_payload, f, indent=2)

    log.info(f"Success! Processed {processed_count} conversion records from SQLite.")
    log.info(f"Heuristic table saved to: {table_path}")
    log.info(f"Heuristic weights saved to: {weights_path}")

    return {
        "heuristic_table": str(table_path),
        "heuristic_weights": str(weights_path),
    }
