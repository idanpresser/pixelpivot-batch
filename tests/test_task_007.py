import pytest
import os
from pathlib import Path
from pydantic import ValidationError
from app.batch_api.models import BatchRequest, Tool

def test_resolve_path_rejects_empty():
    """
    Task 007: _resolve_path should reject empty or whitespace-only strings.
    """
    with pytest.raises(ValidationError) as excinfo:
        BatchRequest(
            source_dir="",
            target_dir="tgt",
            target_format=["webp"],
            tool=[Tool.magick],
            category=["highRes"]
        )
    assert "Path must not be empty" in str(excinfo.value)

    with pytest.raises(ValidationError):
        BatchRequest(source_dir="   ", target_dir="tgt", target_format=["webp"], tool=[Tool.magick])

def test_resolve_path_containment(tmp_path, monkeypatch):
    """
    Task 007: With PIXELPIVOT_ALLOWED_ROOT set, paths outside should be rejected.
    """
    root = tmp_path / "sandbox"
    root.mkdir()
    monkeypatch.setenv("PIXELPIVOT_ALLOWED_ROOT", str(root))

    # Path inside works
    req = BatchRequest(
        source_dir=str(root / "src"),
        target_dir=str(root / "tgt"),
        target_format=["webp"],
        tool=[Tool.magick]
    )
    assert str(root / "src") in req.source_dir

    # Path outside fails
    with pytest.raises(ValidationError) as excinfo:
        BatchRequest(
            source_dir=str(tmp_path / "outside"),
            target_dir=str(root / "tgt"),
            target_format=["webp"],
            tool=[Tool.magick]
        )
    assert "Path escapes the allowed root" in str(excinfo.value)

def test_resolve_path_no_containment_by_default(tmp_path, monkeypatch):
    """
    Task 007: Without PIXELPIVOT_ALLOWED_ROOT, arbitrary paths are still allowed.
    """
    monkeypatch.delenv("PIXELPIVOT_ALLOWED_ROOT", raising=False)
    
    # Path outside (anywhere) works
    req = BatchRequest(
        source_dir=str(tmp_path / "anywhere"),
        target_dir="tgt",
        target_format=["webp"],
        tool=[Tool.magick]
    )
    assert "anywhere" in req.source_dir
