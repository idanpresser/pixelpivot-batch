"""Steel-thread harness #2: converters present+absent, heuristic, egress.

C1-C5 present (live; uses real binaries on this dev host) + absent (simulate
via bogus path) + C6 contract + E1-E3 heuristic + G3 egress static audit.
"""
from __future__ import annotations
import os
import re
import sys
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="pp_audit2_"))
os.environ["PIXELPIVOT_DB_PATH"] = str(_TMP / "audit.db")
PROJ = Path(r"F:\DEV\PixelPivot_202605\pixelpivot_batch")
sys.path.insert(0, str(PROJ))

from PIL import Image


def make_png(path: Path, w: int = 64, h: int = 64) -> None:
    Image.new("RGB", (w, h), color=(200, 60, 120)).save(str(path), format="PNG")


def banner(s: str) -> None:
    print(f"\n=== {s} ===")


def assert_shape(name: str, result: dict, failures: list[str]) -> None:
    expected_keys = {"success_count", "failure_count", "duration_ms", "errors"}
    missing = expected_keys - set(result.keys())
    if missing:
        failures.append(f"C6 {name}: convert_batch result missing keys {missing}; got {list(result.keys())}")
    else:
        print(f"C6 {name}: shape OK (keys={sorted(result.keys())})")


def main() -> int:
    failures: list[str] = []

    fixture = _TMP / "tiny.png"
    make_png(fixture)
    target = _TMP / "out"
    target.mkdir()
    print(f"TMP={_TMP}")

    from app.core.converters.magick_converter import MagickConverter
    from app.core.converters.vips_converter import VipsConverter
    from app.core.converters.ffmpeg_converter import FFmpegConverter
    from app.core.converters.sharp_converter import SharpConverter
    from app.core.converters.ffmpeg_nvenc_converter import FFmpegNvencConverter

    # ---------------------------------------------------------------- C1 magick
    banner("C1 magick PRESENT (webp + avif)")
    mc = MagickConverter(magick_path="magick")
    for fmt, q in [("webp", 80.5), ("avif", 55.0)]:
        out = target / f"magick_present.{fmt}"
        try:
            r = mc.convert(str(fixture), str(out), fmt, q)
            ok = r.get("success") and out.exists() and out.stat().st_size > 0
            print(f"C1 magick {fmt} q={q}: success={r.get('success')} size={out.stat().st_size if out.exists() else 0} err={r.get('error')}")
            if not ok:
                failures.append(f"C1 magick {fmt}: not ok ({r.get('error')})")
        except Exception as e:
            failures.append(f"C1 magick {fmt}: raised {e!r}")

    banner("C1 magick ABSENT (bogus path)")
    mc_bogus = MagickConverter(magick_path=r"C:\definitely\not\magick.exe")
    out = target / "magick_absent.webp"
    r = mc_bogus.convert(str(fixture), str(out), "webp", 80.0)
    print(f"C1 magick ABSENT: success={r.get('success')} err={(r.get('error') or '')[:160]}")
    if r.get("success"):
        failures.append("C1 magick ABSENT: should not succeed with bogus binary")
    # Should NOT raise; should return a dict with success=False
    if not isinstance(r, dict):
        failures.append(f"C1 magick ABSENT: did not return a dict (got {type(r)})")

    banner("C1 magick convert_batch (default + native)")
    out_dir = _TMP / "batch_magick"
    out_dir.mkdir()
    result = mc.convert_batch(
        [str(fixture)], str(out_dir), "webp", [80.0], suffix="_x"
    )
    print(f"C1 batch result: {result}")
    assert_shape("magick", result, failures)
    if result.get("success_count") != 1:
        failures.append(f"C1 batch: expected success_count=1, got {result}")

    # ---------------------------------------------------------------- C3 vips
    banner("C3 vips PRESENT (webp + avif)")
    vc = VipsConverter()
    for fmt, q in [("webp", 80.5), ("avif", 55.0)]:
        out = target / f"vips_present.{fmt}"
        try:
            r = vc.convert(str(fixture), str(out), fmt, q)
            ok = r.get("success") and out.exists() and out.stat().st_size > 0
            params = r.get("parameters_used") or {}
            print(f"C3 vips {fmt} q={q}: success={r.get('success')} size={out.stat().st_size if out.exists() else 0} params={params}")
            if not ok:
                failures.append(f"C3 vips {fmt}: not ok ({r.get('error')})")
            # N6 evidence: if quality stored as int 80 (instead of 80.5), record it
            recorded_q = params.get("Q")
            if recorded_q is not None and isinstance(recorded_q, int) and recorded_q == int(q):
                print(f"C3 vips {fmt}: N6 violation confirmed - recorded Q={recorded_q} (input was {q})")
        except Exception as e:
            failures.append(f"C3 vips {fmt}: raised {e!r}")

    banner("C3 vips ABSENT (simulate via patch)")
    # pyvips is loaded at import; "absent" is when libvips DLL fails to load.
    # We can simulate by patching the module's pyvips reference.
    import app.core.utils as utils_mod
    saved = utils_mod.pyvips
    try:
        utils_mod.pyvips = None
        from app.core.converters import vips_converter as vc_mod
        saved2 = vc_mod.pyvips
        vc_mod.pyvips = None
        out = target / "vips_absent.webp"
        r = vc.convert(str(fixture), str(out), "webp", 80.0)
        print(f"C3 vips ABSENT: success={r.get('success')} err={(r.get('error') or '')[:160]}")
        if r.get("success"):
            failures.append("C3 vips ABSENT: should not succeed with pyvips=None")
        vc_mod.pyvips = saved2
    finally:
        utils_mod.pyvips = saved

    # ---------------------------------------------------------------- C2 ffmpeg
    banner("C2 ffmpeg PRESENT (webp + avif)")
    fc = FFmpegConverter(ffmpeg_path="ffmpeg")
    for fmt, q in [("webp", 80.0), ("avif", 30.0)]:
        out = target / f"ffmpeg_present.{fmt}"
        try:
            r = fc.convert(str(fixture), str(out), fmt, q)
            ok = r.get("success") and out.exists() and out.stat().st_size > 0
            print(f"C2 ffmpeg {fmt} q={q}: success={r.get('success')} size={out.stat().st_size if out.exists() else 0}")
            if not ok:
                failures.append(f"C2 ffmpeg {fmt}: not ok ({(r.get('error') or '')[:160]})")
        except Exception as e:
            failures.append(f"C2 ffmpeg {fmt}: raised {e!r}")

    banner("C2 ffmpeg ABSENT (bogus binary)")
    fc_bogus = FFmpegConverter(ffmpeg_path=r"C:\definitely\not\ffmpeg.exe")
    out = target / "ffmpeg_absent.webp"
    try:
        r = fc_bogus.convert(str(fixture), str(out), "webp", 80.0)
        print(f"C2 ffmpeg ABSENT: success={r.get('success')} err={(r.get('error') or '')[:160]}")
        if r.get("success"):
            failures.append("C2 ffmpeg ABSENT: should not succeed")
    except Exception as e:
        # Acceptable if returns False; raising is also tolerable for the orchestrator
        # since execute_batch wraps in try/except. But the prompt prefers a clean dict.
        print(f"C2 ffmpeg ABSENT: raised {type(e).__name__}: {str(e)[:160]}")
        failures.append(f"C2 ffmpeg ABSENT: raised instead of returning dict: {type(e).__name__}")

    # ---------------------------------------------------------------- C5 nvenc
    banner("C5 ffmpeg_nvenc PRESENT (avif via av1_nvenc)")
    nc = FFmpegNvencConverter(ffmpeg_path="ffmpeg")
    out = target / "nvenc_present.avif"
    try:
        r = nc.convert(str(fixture), str(out), "avif", 80.0, use_gpu=True)
        print(f"C5 nvenc avif: success={r.get('success')} size={out.stat().st_size if out.exists() else 0} err={(r.get('error') or '')[:200]}")
        if not r.get("success"):
            # Not necessarily a failure — av1_nvenc requires Ada Lovelace+
            print("C5 nvenc: present-thread did not succeed; recording as BLOCKED-on-target if encoder not available")
    except Exception as e:
        failures.append(f"C5 nvenc: raised {e!r}")

    banner("C5 ffmpeg_nvenc ABSENT (fatal marker on bogus binary)")
    nc_bogus = FFmpegNvencConverter(ffmpeg_path=r"C:\not\ffmpeg.exe")
    out = target / "nvenc_absent.avif"
    try:
        r = nc_bogus.convert(str(fixture), str(out), "avif", 80.0)
        print(f"C5 nvenc ABSENT: success={r.get('success')} err={(r.get('error') or '')[:160]}")
        if r.get("success"):
            failures.append("C5 nvenc ABSENT: should not succeed")
    except Exception as e:
        print(f"C5 nvenc ABSENT: raised {type(e).__name__}: {str(e)[:160]}")
        failures.append(f"C5 nvenc ABSENT: raised instead of returning dict: {type(e).__name__}")

    # ---------------------------------------------------------------- C4 sharp
    banner("C4 sharp PRESENT (daemon may be down)")
    sc = SharpConverter(port=8765)
    out = target / "sharp_present.webp"
    try:
        r = sc.convert(str(fixture), str(out), "webp", 80.0)
        ok = r.get("success") and out.exists() and out.stat().st_size > 0
        print(f"C4 sharp webp: success={r.get('success')} size={out.stat().st_size if out.exists() else 0} err={(r.get('error') or '')[:200]}")
        if not ok:
            print("C4 sharp: PRESENT thread did not succeed; recording as BLOCKED (needs daemon)")
    except Exception as e:
        print(f"C4 sharp: raised {type(e).__name__}: {str(e)[:160]} -- BLOCKED-on-target")

    banner("C4 sharp ABSENT (bogus port = daemon down)")
    sc_bogus = SharpConverter(port=65510)
    out = target / "sharp_absent.webp"
    try:
        r = sc_bogus.convert(str(fixture), str(out), "webp", 80.0)
        print(f"C4 sharp ABSENT: success={r.get('success')} err={(r.get('error') or '')[:160]}")
        if r.get("success"):
            failures.append("C4 sharp ABSENT: should not succeed on a dead port")
    except Exception as e:
        print(f"C4 sharp ABSENT: raised {type(e).__name__}: {str(e)[:160]}")
        failures.append(f"C4 sharp ABSENT: raised instead of returning dict: {type(e).__name__}")

    # ---------------------------------------------------------------- E1-E3 heuristic
    banner("E1/E2/E3 HeuristicInterpolator on the SHIPPED empty table")
    from app.core.heuristic_interpolator import HeuristicInterpolator
    from app.core.paths import APP_ROOT
    from app.core.config import default_quality_for, quality_range_for
    table = APP_ROOT / "core" / "heuristic_table.json"
    hi = HeuristicInterpolator(table)
    print(f"E3 table version: {hi.version}")
    # As-shipped table has only {"version": "2.0.0"} - every lookup must fall back.
    cases = [
        ("general", "webp", "magick", 1920, 1080),
        ("general", "avif", "ffmpeg", 100, 100),
        ("general", "jxl",  "vips",   4000, 3000),
        ("xxx",     "webp", "ffmpeg_nvenc", 1, 1),  # bogus category
    ]
    for cat, fmt, tool, w, h in cases:
        q = hi.get_interpolated_quality(cat, fmt, tool, w, h)
        expected = default_quality_for(tool, fmt)
        lo, hi2 = quality_range_for(tool, fmt)
        within_range = lo <= q <= hi2
        match_default = (q == expected)
        print(f"E1/E2 {cat}/{fmt}/{tool} {w}x{h}: q={q} expected_default={expected} in_range={within_range}")
        if not match_default:
            failures.append(f"E2: empty-table lookup did not fall back to default_quality_for: got {q}, expected {expected}")
        if not within_range:
            failures.append(f"E1: result {q} outside encoder range [{lo}, {hi2}] for {tool}/{fmt}")

    # ---------------------------------------------------------------- G3 egress audit
    banner("G3 egress audit (static)")
    # Grep app/ for hostnames or http/socket calls that imply outbound traffic
    suspicious_patterns = [
        r"https?://(?!127\.0\.0\.1|localhost|0\.0\.0\.0)[a-zA-Z0-9]",
        r"requests\.(get|post|put|patch|delete)",
        r"urllib\.request\.urlopen",
        r"urllib3\.PoolManager",
        r"httpx\.(get|post|put|patch|delete|Client\(\))",
        r"socket\.create_connection\(\([^,]+(?!127\.0\.0\.1|localhost)",
    ]
    hits: list[str] = []
    app_dir = PROJ / "app"
    for py in app_dir.rglob("*.py"):
        try:
            text = py.read_text(encoding="utf-8")
        except Exception:
            continue
        for pat in suspicious_patterns:
            for m in re.finditer(pat, text):
                line_no = text[:m.start()].count("\n") + 1
                # Skip if commented out
                line_start = text.rfind("\n", 0, m.start()) + 1
                line_end = text.find("\n", m.start())
                full_line = text[line_start:line_end if line_end > 0 else None]
                if full_line.strip().startswith("#"):
                    continue
                hits.append(f"{py.relative_to(PROJ)}:{line_no}: {full_line.strip()[:120]}")
    print(f"G3 suspicious lines found: {len(hits)}")
    for h in hits[:30]:
        print(f"  - {h}")
    # Note: hits in vendor/, scripts/, tests/ are not searched (we only look in app/).

    print("\n========================================")
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f" - {f}")
        return 1
    print("ALL CONVERTER/HEURISTIC/EGRESS THREADS COMPLETED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
