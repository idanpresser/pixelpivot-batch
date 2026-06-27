# tests/batch_api/test_image_guards.py
import pytest
from app.batch_api import image_guards
from app.core.config import MASSIVE_IMAGE_THRESHOLD


def test_partition_rejects_unreadable_and_massive():
    paths = ["ok.png", "bad.png", "huge.png"]
    dims = {"ok.png": (100, 100), "bad.png": (0, 0), "huge.png": (10**6, 10**6)}
    usable, errors = image_guards.partition_images(paths, dims)
    assert usable == ["ok.png"]
    reasons = {e["path"]: e["error"] for e in errors}
    assert "unreadable" in reasons["bad.png"].lower()
    assert "massive" in reasons["huge.png"].lower()
    assert (10**6) * (10**6) > MASSIVE_IMAGE_THRESHOLD


def test_preflight_resources_raises_on_low_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(image_guards.shutil, "disk_usage",
                        lambda _p: (0, 0, 1))  # 1 byte free
    with pytest.raises(ValueError):
        image_guards.preflight_resources(str(tmp_path))
