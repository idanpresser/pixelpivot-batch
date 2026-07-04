"""E9 9.2 — optional per-file cross-tool fallback (env-gated, off by default).

When a file fails the primary tool and PIXELPIVOT_FALLBACK_TOOL names an
alternate, the file is retried on that alternate before being counted as a
failure. Disabled by default to keep batches deterministic.
"""
from pathlib import Path

from app.batch_api.orchestrator import (
    apply_cross_tool_fallback,
    cross_tool_fallback_tool,
)


def test_fallback_disabled_by_default(monkeypatch):
    monkeypatch.delenv("PIXELPIVOT_FALLBACK_TOOL", raising=False)
    assert cross_tool_fallback_tool() is None


def test_fallback_tool_read_and_normalized_from_env(monkeypatch):
    monkeypatch.setenv("PIXELPIVOT_FALLBACK_TOOL", " VIPS ")
    assert cross_tool_fallback_tool() == "vips"


def test_apply_fallback_recovers_only_files_the_retry_succeeds_on():
    errors = [{"path": "a.png", "error": "boom"}, {"path": "b.png", "error": "boom"}]
    recovered, remaining = apply_cross_tool_fallback(errors, lambda p: p == "a.png")
    assert recovered == ["a.png"]
    assert remaining == [{"path": "b.png", "error": "boom"}]


def test_apply_fallback_passes_through_pathless_errors():
    errors = [{"path": None, "error": "no path"}]
    recovered, remaining = apply_cross_tool_fallback(errors, lambda p: True)
    assert recovered == []
    assert remaining == errors


class _StubConv:
    """Minimal converter double: records the convert() call and succeeds."""
    def __init__(self):
        self.is_broken = False
        self.calls = []

    def convert(self, in_path, out_path, target_format, quality, run_id=None):
        self.calls.append((in_path, out_path, target_format, quality))
        Path(out_path).write_bytes(b"ok")

        class _Res:
            success = True
        return _Res()


def test_fallback_retry_one_invokes_alternate_converter(tmp_path, monkeypatch):
    from app.batch_api.orchestrator import BatchOrchestrator

    orch = BatchOrchestrator.__new__(BatchOrchestrator)  # skip heavy __init__
    alt = _StubConv()
    orch.converters = {"vips": alt}

    src = tmp_path / "img.png"
    src.write_bytes(b"data")
    ok = orch._fallback_retry_one(
        str(src), alt_tool="vips", target_dir=str(tmp_path),
        target_format="webp", quality=80.0, suffix="_magick", run_id=1,
    )
    assert ok is True
    assert len(alt.calls) == 1
    # Output named for the cell it is filling in for (same suffix).
    assert alt.calls[0][1].endswith("img_magick.webp")
