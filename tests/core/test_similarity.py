# tests/core/test_similarity.py
import numpy as np
from app.core import similarity


def test_compute_ssim_identical_is_one():
    a = np.random.default_rng(0).integers(0, 256, (64, 64, 3)).astype(np.uint8)
    assert similarity.compute_ssim(a, a) > 0.999


def test_compute_ssim_degraded_is_lower():
    a = np.full((64, 64, 3), 128, np.uint8)
    b = a.copy()
    b[::2] = 0
    assert similarity.compute_ssim(a, b) < similarity.compute_ssim(a, a)


def test_score_ssim_decode_failure_returns_sentinel(monkeypatch):
    def boom(_path):
        raise RuntimeError("undecodable")
    monkeypatch.setattr(similarity, "decode_rgb", boom)
    assert similarity.score_ssim("orig.png", "cand.webp") == -1.0


def test_score_ssim_shape_mismatch_returns_sentinel(monkeypatch):
    monkeypatch.setattr(similarity, "decode_rgb", lambda _p: np.zeros((10, 10, 3), np.uint8))
    orig = np.zeros((20, 20, 3), np.uint8)
    assert similarity.score_ssim("o", "c", orig_rgb=orig) == -1.0
