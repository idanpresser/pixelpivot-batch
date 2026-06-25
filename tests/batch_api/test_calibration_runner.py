# tests/batch_api/test_calibration_runner.py
import importlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

pytestmark = pytest.mark.integration


def _make_image(path, seed):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:240, 0:320]
    base = (np.sin(xx / 20.0) + np.cos(yy / 18.0)) * 40 + 128
    arr = np.stack([base, base * 0.9 + 20, base * 0.8 + 40], -1)
    arr = (arr + rng.normal(0, 5, arr.shape)).clip(0, 255).astype(np.uint8)
    Image.fromarray(arr).save(path)


def test_run_calibration_writes_conversions_and_regenerates(tmp_path, monkeypatch):
    db_path = tmp_path / "calib.db"
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(db_path))

    # Rebind modules that captured the DB path / gate at import time.
    from app.core.db import connection as db_connection
    importlib.reload(db_connection)
    from app.core.db import schema as db_schema
    importlib.reload(db_schema)
    db_schema.init_db()

    from app.core import config
    monkeypatch.setattr(config, "CALIBRATION_ENABLED", True)
    monkeypatch.setattr(config, "HEURISTIC_MIN_SAMPLES", 1)

    src = tmp_path / "samples"
    src.mkdir()
    for i in range(2):
        _make_image(src / f"img_{i}.png", seed=i)

    table_out = tmp_path / "heuristic_table.json"

    from app.batch_api import calibration_runner

    # Skip if the chosen encoder is not available in this environment.
    orch = calibration_runner.BatchOrchestrator()
    probe = orch.converters["vips"].convert(
        str(src / "img_0.png"), str(tmp_path / "probe.webp"), "webp", 80, is_intermediate=True
    )
    if not probe.get("success"):
        pytest.skip(f"vips/webp encoder unavailable: {probe.get('error')}")

    summary = calibration_runner.run_calibration(
        str(src), ["general"], ["vips"], ["webp"],
        sample=10, target_ssim=0.95,
        regenerate_table=False,  # call generator explicitly with the low gate below
    )

    assert summary["calibrated"] >= 1

    from app.core.db import get_connection
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT quality, calib_method FROM conversions WHERE success = 1"
        ).fetchall()
        assert len(rows) >= 1
        assert all(r["calib_method"] == "ssim" for r in rows)
        cal_rows = conn.execute("SELECT COUNT(*) AS n FROM calibration_results").fetchone()
        assert cal_rows["n"] >= 1

    from app.core.heuristic import generate_heuristic_table
    with get_connection() as conn:
        result = generate_heuristic_table(
            conn=conn, table_path=table_out, weights_path=tmp_path / "w.json"
        )
    import json
    table = json.loads(Path(result["heuristic_table"]).read_text())
    assert "general" in table and "webp" in table["general"]
