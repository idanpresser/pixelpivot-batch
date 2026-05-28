"""
Integration tests for FFmpegConverter.convert_batch().

Requires ffmpeg installed and on PATH. Tests use tiny PNG inputs generated
in tmp_path so they are hermetic but exercise the real subprocess pipeline.
"""

import shutil
from pathlib import Path

import pytest
from PIL import Image

from app.core.converters.ffmpeg_converter import FFmpegConverter


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg binary required for integration test",
)


def _make_png(path: Path, w: int, h: int, color: tuple = (255, 0, 0)) -> str:
    img = Image.new("RGB", (w, h), color)
    img.save(str(path), "PNG")
    return str(path)


def _make_noisy_png(path: Path, w: int, h: int) -> str:
    """Create a PNG with pseudo-random noise so quality setting affects file size."""
    import random

    rng = random.Random(42)
    img = Image.new("RGB", (w, h))
    pixels = [
        (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255))
        for _ in range(w * h)
    ]
    img.putdata(pixels)
    img.save(str(path), "PNG")
    return str(path)


def test_batch_uniform_size_uses_image2_path(tmp_path):
    out_dir = tmp_path / "out"
    inputs = [_make_png(tmp_path / f"img_{i}.png", 320, 240) for i in range(5)]

    conv = FFmpegConverter(ffmpeg_path=shutil.which("ffmpeg"))
    result = conv.convert_batch(
        input_paths=inputs,
        output_dir=str(out_dir),
        target_format="webp",
        qualities=[80.0] * 5,
    )

    assert result["success_count"] == 5
    assert result["failure_count"] == 0
    for i in range(5):
        assert (out_dir / f"img_{i}.webp").exists()
        assert (out_dir / f"img_{i}.webp").stat().st_size > 0


def test_batch_mixed_sizes_uses_multimap_path(tmp_path):
    out_dir = tmp_path / "out"
    inputs = [
        _make_png(tmp_path / "small.png", 100, 100),
        _make_png(tmp_path / "med.png",   400, 300),
        _make_png(tmp_path / "big.png",   800, 600),
    ]

    conv = FFmpegConverter(ffmpeg_path=shutil.which("ffmpeg"))
    result = conv.convert_batch(
        input_paths=inputs,
        output_dir=str(out_dir),
        target_format="webp",
        qualities=[75.0, 75.0, 75.0],
    )

    assert result["success_count"] == 3
    for name in ("small.webp", "med.webp", "big.webp"):
        assert (out_dir / name).exists()


def test_batch_mixed_qualities_separates_groups(tmp_path):
    out_dir = tmp_path / "out"
    # Use noisy images so that quality 40 vs 90 produces meaningfully different sizes.
    inputs = [_make_noisy_png(tmp_path / f"img_{i}.png", 200, 200) for i in range(4)]

    conv = FFmpegConverter(ffmpeg_path=shutil.which("ffmpeg"))
    result = conv.convert_batch(
        input_paths=inputs,
        output_dir=str(out_dir),
        target_format="webp",
        qualities=[40.0, 40.0, 90.0, 90.0],
    )

    assert result["success_count"] == 4
    low  = (out_dir / "img_0.webp").stat().st_size
    high = (out_dir / "img_2.webp").stat().st_size
    assert low < high


def test_batch_returns_required_keys(tmp_path):
    out_dir = tmp_path / "out"
    inputs = [_make_png(tmp_path / "img_0.png", 200, 200)]

    conv = FFmpegConverter(ffmpeg_path=shutil.which("ffmpeg"))
    result = conv.convert_batch(
        input_paths=inputs,
        output_dir=str(out_dir),
        target_format="webp",
        qualities=[80.0],
    )

    for key in ("success_count", "failure_count", "duration_ms", "telemetry", "errors"):
        assert key in result


def test_unprobeable_file_routes_to_per_file_fallback(tmp_path):
    """A file masquerading as PNG but with garbage bytes will fail
    probe_image_dimensions, land in the None bucket, and run through
    per-file convert() (which will also fail, but cleanly)."""
    out_dir = tmp_path / "out"
    good = _make_png(tmp_path / "good.png", 200, 200)
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not a real png at all")

    conv = FFmpegConverter(ffmpeg_path=shutil.which("ffmpeg"))
    result = conv.convert_batch(
        input_paths=[good, str(bad)],
        output_dir=str(out_dir),
        target_format="webp",
        qualities=[80.0, 80.0],
    )

    # Good file converts; bad file fails cleanly without raising.
    assert result["success_count"] == 1
    assert result["failure_count"] == 1
    assert any(e["path"] == str(bad) for e in result["errors"])
    assert (out_dir / "good.webp").exists()


def test_image2_threshold_routes_two_files_to_multimap(tmp_path):
    """With IMAGE2_THRESHOLD=3, a uniform-size group of 2 should still
    succeed via the multimap path."""
    out_dir = tmp_path / "out"
    inputs = [_make_png(tmp_path / f"img_{i}.png", 256, 256) for i in range(2)]

    conv = FFmpegConverter(ffmpeg_path=shutil.which("ffmpeg"))
    result = conv.convert_batch(
        input_paths=inputs,
        output_dir=str(out_dir),
        target_format="webp",
        qualities=[80.0, 80.0],
    )
    assert result["success_count"] == 2
    assert (out_dir / "img_0.webp").exists()
    assert (out_dir / "img_1.webp").exists()


def test_equality_recheck_disagreement_falls_back_to_multimap(tmp_path, monkeypatch):
    """If group_by_dimensions and all_same_resolution disagree (simulated by
    monkeypatching the re-check to always return False), the orchestrator
    must route the work to the multimap path instead of image2, and every
    file must still be converted successfully."""
    out_dir = tmp_path / "out"
    inputs = [_make_png(tmp_path / f"img_{i}.png", 400, 400) for i in range(5)]

    monkeypatch.setattr(
        "app.core.converters.ffmpeg_converter.all_same_resolution",
        lambda paths: False,
    )

    conv = FFmpegConverter(ffmpeg_path=shutil.which("ffmpeg"))
    result = conv.convert_batch(
        input_paths=inputs,
        output_dir=str(out_dir),
        target_format="webp",
        qualities=[80.0] * 5,
    )

    assert result["success_count"] == 5
    assert result["failure_count"] == 0
    for i in range(5):
        assert (out_dir / f"img_{i}.webp").exists()


def test_telemetry_is_present_and_aggregated(tmp_path):
    """Per-image telemetry is acknowledged-lost; per-batch averaging is the
    contract. Verify aggregate_telemetry's expected keys are populated."""
    out_dir = tmp_path / "out"
    inputs = [_make_png(tmp_path / f"img_{i}.png", 300, 300) for i in range(5)]

    conv = FFmpegConverter(ffmpeg_path=shutil.which("ffmpeg"))
    result = conv.convert_batch(
        input_paths=inputs,
        output_dir=str(out_dir),
        target_format="webp",
        qualities=[80.0] * 5,
    )

    telemetry = result["telemetry"]
    assert isinstance(telemetry, dict)
    # aggregate_telemetry returns at minimum these keys.
    for key in ("cpu_avg", "cpu_peak", "ram_peak"):
        assert key in telemetry, f"telemetry missing key {key}"


def test_batch_against_real_test_examples(tmp_path):
    """End-to-end smoke test against the project's real test_examples folder.
    Picks the first 10 web_*.jpg files (which tend to share similar dimensions)
    and confirms convert_batch can handle a real-world mixed-resolution batch."""
    repo_root = Path(__file__).parent.parent
    examples_dir = repo_root / "test_examples"
    if not examples_dir.exists():
        pytest.skip("test_examples folder not present")

    inputs = sorted(str(p) for p in examples_dir.glob("web_*.jpg"))[:10]
    if len(inputs) < 5:
        pytest.skip(f"need at least 5 real test images, found {len(inputs)}")

    out_dir = tmp_path / "out"
    conv = FFmpegConverter(ffmpeg_path=shutil.which("ffmpeg"))
    result = conv.convert_batch(
        input_paths=inputs,
        output_dir=str(out_dir),
        target_format="webp",
        qualities=[80.0] * len(inputs),
    )

    # All inputs should be converted (image2 or multimap or per-file).
    assert result["success_count"] == len(inputs), (
        f"Expected {len(inputs)} successes, got "
        f"{result['success_count']} / {result['failure_count']} failures: "
        f"{result['errors']}"
    )
    for in_path in inputs:
        stem = Path(in_path).stem
        assert (out_dir / f"{stem}.webp").exists()
        assert (out_dir / f"{stem}.webp").stat().st_size > 0
