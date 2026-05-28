import json
import math
from pathlib import Path

from app.core.heuristic_interpolator import HeuristicInterpolator


def test_shipped_table_loads_with_current_version():
    # The shipped table ships without priors (regenerated from a real DB later);
    # it must still load and carry the current schema version.
    from app.core.config import HEURISTIC_TABLE_VERSION

    table_path = Path("app/core/heuristic_table.json")
    table = json.loads(table_path.read_text())
    assert table.get("version") == HEURISTIC_TABLE_VERSION
    # Any cell present must be curve-shaped, never a bare bucket scalar.
    for cat, fmts in table.items():
        if cat == "version":
            continue
        for fmt, tools in fmts.items():
            for tool, cell in tools.items():
                assert set(("a", "b", "n", "mp_min", "mp_max")).issubset(cell)


def test_curve_interpolator_varies_with_resolution(tmp_path):
    # A fitted curve must give different qualities at different resolutions.
    table_path = tmp_path / "t.json"
    data = {
        "version": "2.0.0",
        "general": {"avif": {"ffmpeg": {"a": 30.0, "b": 4.0, "n": 50, "mp_min": 0.05, "mp_max": 20.0}}},
    }
    table_path.write_text(json.dumps(data))
    interp = HeuristicInterpolator(table_path)

    q_small = interp.get_interpolated_quality("general", "avif", "ffmpeg", 400, 300)
    q_large = interp.get_interpolated_quality("general", "avif", "ffmpeg", 4000, 3000)
    assert q_small != q_large
