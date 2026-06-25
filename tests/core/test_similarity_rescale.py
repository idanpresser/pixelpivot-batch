# tests/core/test_similarity_rescale.py
import numpy as np
from app.core import similarity, config


def test_score_ssim_rescales_huge_images(monkeypatch):
    # Force the huge threshold low so a small array trips the rescale path.
    monkeypatch.setattr(config, "HUGE_IMAGE_THRESHOLD", 10)
    seen = {}
    real = similarity.compute_ssim

    def spy(a, b):
        seen["shape"] = a.shape
        return real(a, b)

    monkeypatch.setattr(similarity, "compute_ssim", spy)
    a = np.full((40, 40, 3), 128, np.uint8)
    monkeypatch.setattr(similarity, "decode_rgb", lambda _p: a)
    similarity.score_ssim("o", "c", orig_rgb=a)
    # 40*40 = 1600 > 10 -> downscaled below original area
    assert seen["shape"][0] * seen["shape"][1] < 40 * 40
