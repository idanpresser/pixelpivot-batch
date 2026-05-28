import pytest
import json
import math
from pathlib import Path
from app.core.heuristic_interpolator import HeuristicInterpolator


@pytest.fixture
def curve_table(tmp_path):
    """A fitted log-linear curve: quality = 85 - 10*log10(MP) over [0.1, 50] MP."""
    table_path = tmp_path / "heuristic_table.json"
    data = {
        "version": "2.0.0",
        "general": {
            "webp": {
                "magick": {"a": 85.0, "b": -10.0, "n": 50, "mp_min": 0.1, "mp_max": 50.0}
            }
        },
    }
    table_path.write_text(json.dumps(data))
    return table_path


@pytest.fixture
def interpolator(curve_table):
    return HeuristicInterpolator(curve_table)


def _law(mp):
    return 85.0 - 10.0 * math.log10(mp)


def test_curve_evaluated_at_image_mp(interpolator):
    # 1.0 MP (1000x1000): log10(1)=0 -> a = 85.0
    assert interpolator.get_interpolated_quality("general", "webp", "magick", 1000, 1000) == pytest.approx(_law(1.0), abs=0.01)
    # 4.0 MP (2000x2000)
    assert interpolator.get_interpolated_quality("general", "webp", "magick", 2000, 2000) == pytest.approx(_law(4.0), abs=0.01)


def test_intermediate_mp_follows_curve(interpolator):
    # 3.5 MP (2000x1750): a value the old 4-bucket interpolation could not place.
    assert interpolator.get_interpolated_quality("general", "webp", "magick", 2000, 1750) == pytest.approx(_law(3.5), abs=0.01)


def test_clamps_to_observed_mp_range(interpolator):
    # 0.01 MP (100x100) is below mp_min (0.1): evaluate at mp_min, not below it.
    assert interpolator.get_interpolated_quality("general", "webp", "magick", 100, 100) == pytest.approx(_law(0.1), abs=0.01)
    # 100 MP is above mp_max (50.0): evaluate at mp_max.
    assert interpolator.get_interpolated_quality("general", "webp", "magick", 10000, 10000) == pytest.approx(_law(50.0), abs=0.01)


def test_missing_data_fallbacks(interpolator, tmp_path):
    empty_path = tmp_path / "empty.json"
    empty_path.write_text(json.dumps({}))
    interp_empty = HeuristicInterpolator(empty_path)
    # No curve for this combo -> tool/format-native default (generic 80.0).
    assert interp_empty.get_interpolated_quality("any", "any", "any", 1000, 1000) == 80.0


def test_result_clamped_to_encoder_range(tmp_path):
    # ffmpeg avif is a CRF (0..63); a curve that evaluates above 63 must be clamped.
    table_path = tmp_path / "t.json"
    data = {
        "version": "2.0.0",
        "general": {"avif": {"ffmpeg": {"a": 100.0, "b": 0.0, "n": 10, "mp_min": 0.1, "mp_max": 50.0}}},
    }
    table_path.write_text(json.dumps(data))
    interp = HeuristicInterpolator(table_path)
    assert interp.get_interpolated_quality("general", "avif", "ffmpeg", 1000, 1000) == 63.0
