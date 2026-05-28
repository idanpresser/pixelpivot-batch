"""Task 024 - converter code must not int()-cast fractional quality.

The heuristic interpolator emits `q = a + b * log10(MP)` (a fractional
0..100 or 0..63). Casting to int silently rounds DOWN and biases the
recorded analytics, which the feedback loop then re-learns from.
Use round() only at the very last boundary where the encoder requires
an integer scalar.
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parent.parent
CONVERTERS = PROJ / "app" / "core" / "converters"


def _converter_sources() -> list[Path]:
    return sorted(CONVERTERS.rglob("*.py"))


def test_no_int_cast_of_quality_in_converter_sources() -> None:
    """Static grep: `int(quality)` is forbidden inside app/core/converters/."""
    pat_quality = re.compile(r"int\s*\(\s*quality\b")
    pat_q_lambda = re.compile(r"int\s*\(\s*\(\s*100\s*-\s*q\b")
    offenders: list[str] = []
    for src in _converter_sources():
        text = src.read_text(encoding="utf-8")
        for ln, line in enumerate(text.splitlines(), 1):
            if pat_quality.search(line) or pat_q_lambda.search(line):
                offenders.append(f"{src.relative_to(PROJ)}:{ln}: {line.strip()}")
    assert not offenders, "int() cast of quality found:\n" + "\n".join(offenders)


def test_vips_webpsave_records_fractional_quality(tmp_path) -> None:
    """pyvips conversion records the float quality, not the truncated int."""
    pyvips_mod = pytest.importorskip("pyvips")
    from PIL import Image
    src = tmp_path / "in.png"
    out = tmp_path / "out.webp"
    Image.new("RGB", (32, 32), color=(60, 120, 200)).save(str(src), format="PNG")

    from app.core.converters.vips_converter import VipsConverter
    vc = VipsConverter()
    res = vc.convert(str(src), str(out), "webp", 80.5)
    assert res.get("success"), f"vips webp conversion failed: {res.get('error')}"
    params = res.get("parameters_used", {}) or {}
    q_recorded = params.get("Q")
    assert q_recorded is not None, f"no Q in parameters_used: {params}"
    # Must be fractional (the input was 80.5). If it's int(80.5)=80, fail.
    assert q_recorded == pytest.approx(80.5), (
        f"recorded Q={q_recorded} (type {type(q_recorded).__name__}); "
        f"expected 80.5 -- int() cast bug present"
    )


def test_nvenc_lambda_uses_round_not_truncate() -> None:
    """At q=86.6 the formula (100-q)/2 = 6.7 -- round=7, int=6. Must be 7."""
    from app.core.converters.ffmpeg_nvenc_converter import FFmpegNvencConverter
    avif_args = FFmpegNvencConverter.FORMAT_PARAMS["avif"](86.6)
    # Find the value following "-cq"
    cq_idx = avif_args.index("-cq")
    cq_val = avif_args[cq_idx + 1]
    assert cq_val == "7", (
        f"-cq value for q=86.6 was {cq_val!r}; expected '7' (round of 6.7), "
        f"not '6' (truncate)"
    )
    # Boundary clamp still works
    extreme = FFmpegNvencConverter.FORMAT_PARAMS["avif"](-999)
    cq_extreme = extreme[extreme.index("-cq") + 1]
    assert int(cq_extreme) <= 51, f"CQ must clamp to <= 51; got {cq_extreme}"


def test_magick_wand_uses_round() -> None:
    """The Wand fallback in MagickConverter must use round(quality) (still
    int-valued, but unbiased)."""
    src = (PROJ / "app" / "core" / "converters" / "magick_converter.py").read_text(
        encoding="utf-8"
    )
    # Look for the Wand assignment line; it must use round, not int.
    m = re.search(r"img\.quality\s*=\s*(\w+)\s*\(", src)
    assert m, "could not locate img.quality assignment in magick_converter.py"
    fn = m.group(1)
    assert fn == "round", f"expected round(); got {fn}() for img.quality assignment"


def test_nvenc_converter_imports_optional() -> None:
    """ffmpeg_nvenc_converter.py uses Optional[int] on its convert signature
    but did not import it; only `from __future__ import annotations` saves
    it. Fix that to make the typing-honest."""
    src = (PROJ / "app" / "core" / "converters" / "ffmpeg_nvenc_converter.py").read_text(
        encoding="utf-8"
    )
    assert re.search(
        r"from\s+typing\s+import[^\n]*\bOptional\b", src
    ), "ffmpeg_nvenc_converter.py must import Optional from typing"
