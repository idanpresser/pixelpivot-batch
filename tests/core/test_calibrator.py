# tests/core/test_calibrator.py
from pathlib import Path

from app.core import calibrator


class FakeConverter:
    """Records the quality used per output path; writes a size-proportional file."""

    def __init__(self, name):
        self._name = name
        self.q_by_path = {}

    def get_name(self):
        return self._name

    def convert(self, inp, out, fmt, quality, is_intermediate=False, run_id=None):
        size = max(1, int(round(float(quality))) + 1)
        Path(out).write_bytes(b"x" * size)
        self.q_by_path[out] = float(quality)
        return {
            "success": True,
            "fatal_error": False,
            "duration_ms": 1.0,
            "bytes_written": size,
        }


def _scorer(conv, model):
    def score(_orig, conv_path, *, orig_rgb=None):
        return model(conv.q_by_path[conv_path])
    return score


def test_ascending_converges_to_target(tmp_path):
    conv = FakeConverter("vips")
    model = lambda q: min(1.0, 0.90 + 0.001 * q)  # q=80 -> 0.98
    res = calibrator.find_optimal_quality(
        conv, "in.png", "webp", "vips", str(tmp_path),
        target_ssim=0.98, initial_quality=50, score_fn=_scorer(conv, model),
    )
    assert res.get("quality_found") is not None
    assert res["ssim_achieved"] >= 0.98 - 1e-9
    assert 75 <= res["quality_found"] <= 92


def test_descending_crf_converges_to_target(tmp_path):
    conv = FakeConverter("ffmpeg")
    model = lambda crf: min(1.0, 1.0 - 0.005 * crf)  # crf=4 -> 0.98
    res = calibrator.find_optimal_quality(
        conv, "in.png", "avif", "ffmpeg", str(tmp_path),
        target_ssim=0.98, initial_quality=20, score_fn=_scorer(conv, model),
    )
    assert res.get("quality_found") is not None
    assert res["ssim_achieved"] >= 0.98 - 1e-9
    assert res["quality_found"] <= 10


def test_unreachable_target_returns_best_effort_capped(tmp_path):
    conv = FakeConverter("vips")
    res = calibrator.find_optimal_quality(
        conv, "in.png", "webp", "vips", str(tmp_path),
        target_ssim=0.99, max_iters=6, score_fn=_scorer(conv, lambda q: 0.5),
    )
    assert res.get("quality_found") is not None
    assert res["iterations"] <= 6


def test_all_points_fail_returns_error(tmp_path):
    conv = FakeConverter("vips")
    res = calibrator.find_optimal_quality(
        conv, "in.png", "webp", "vips", str(tmp_path),
        score_fn=lambda *a, **k: -1.0,
    )
    assert "error" in res
    assert res.get("quality_found") is None
