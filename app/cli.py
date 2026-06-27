import argparse
import sys
import os
import shutil
import socket
from pathlib import Path

# Frozen-aware project root (PyInstaller bundles binaries next to the exe).
# Centralised in app.core.paths so the seam lives in exactly one place.
from app.core.paths import PROJ_ROOT
from app.core.utils import ensure_vips_dlls
ensure_vips_dlls()

from app.core import toolcheck

def check_binary(name: str, path_str: str) -> bool:
    """Check if a binary exists at the path or is on PATH."""
    print(f"Checking {name}...", end="", flush=True)
    st = toolcheck.check_binary(name, path_str)
    if st.ok:
        if st.detail == path_str:
            print(f" OK (found at {path_str})")
        else:
            print(f" OK (found on PATH at {st.detail})")
    else:
        print(" FAILED (not found)")
    return st.ok

def check_pyvips() -> bool:
    """Check if pyvips/libvips is available."""
    print("Checking pyvips/libvips...", end="", flush=True)
    st = toolcheck.check_pyvips()
    if st.ok:
        print(f" OK (libvips version {st.version})")
    else:
        print(f" FAILED ({st.detail})")
    return st.ok

def check_sharp_daemon(port: int = 8765) -> bool:
    """Check if the Sharp daemon is listening on the port."""
    print(f"Checking Sharp daemon (port {port})...", end="", flush=True)
    st = toolcheck.check_sharp_daemon(port)
    if st.ok:
        print(" OK (connected)")
    else:
        # Extract exception message from down ({e})
        err_msg = st.detail[6:-1] if (st.detail and st.detail.startswith("down (") and st.detail.endswith(")")) else st.detail
        print(f" WARNING (could not connect: {err_msg})")
    return st.ok

def check_sharp_install() -> bool:
    """Check if Node.js and sharp module are installed."""
    print("Checking Sharp installation...", end="", flush=True)
    st = toolcheck.check_sharp_install()
    if st.ok:
        print(f" OK ({st.detail})")
    else:
        print(f" WARNING ({st.detail})")
    return st.ok

def check_paths(source: str, target: str) -> bool:
    """Validate source and target paths and write permissions."""
    ok = True
    
    # Check source
    print(f"Checking source directory '{source}'...", end="", flush=True)
    src_path = Path(source)
    if not src_path.exists():
        print(" FAILED (directory does not exist)")
        ok = False
    elif not src_path.is_dir():
        print(" FAILED (path is not a directory)")
        ok = False
    else:
        # Check readability by trying to list dir
        try:
            list(src_path.iterdir())
            print(" OK (readable)")
        except Exception as e:
            print(f" FAILED (not readable: {e})")
            ok = False
            
    # Check target
    print(f"Checking target directory '{target}'...", end="", flush=True)
    tgt_path = Path(target)
    try:
        tgt_path.mkdir(parents=True, exist_ok=True)
        # Try to write and delete a temp file to test write permissions
        temp_file = tgt_path / ".pixelpivot_write_test"
        temp_file.write_text("test")
        temp_file.unlink()
        print(" OK (creatable/writable)")
    except Exception as e:
        print(f" FAILED (not writable: {e})")
        ok = False
        
    return ok

def main(argv=None):
    parser = argparse.ArgumentParser(description="PixelPivot Batch Engine.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="Run the FastAPI API server.")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--allow-public", action="store_true", help="Allow public interface exposure.")

    p_conv = sub.add_parser("convert", help="Validate environment / run conversion.")
    p_conv.add_argument("--source", "-s", required=True)
    p_conv.add_argument("--target", "-t", required=True)
    p_conv.add_argument("--dry-run", action="store_true")

    sub.add_parser("tui", help="Launch the terminal UI (supervises the API).")

    p_gui = sub.add_parser("gui", help="Launch the Streamlit dashboard GUI.")
    p_gui.add_argument("--port", type=int, default=8503)
    p_gui.add_argument("--api-url", default=None, help="BATCH_API_URL the GUI should target.")

    sub.add_parser("doctor", help="Check system environment and dependencies.")

    p_cal = sub.add_parser("calibrate", help="Serial SSIM calibration; regenerates the heuristic table.")
    p_cal.add_argument("--source", "-s", required=True, help="Directory of sample images.")
    p_cal.add_argument("--tools", default="magick,ffmpeg,vips,sharp", help="Comma-separated tools.")
    p_cal.add_argument("--formats", default="webp,avif,jxl", help="Comma-separated target formats.")
    p_cal.add_argument("--categories", default="general", help="Comma-separated categories.")
    p_cal.add_argument("--sample", type=int, default=30, help="Max images per matrix cell.")
    p_cal.add_argument("--target-ssim", type=float, default=0.98, help="Target SSIM.")
    p_cal.add_argument("--no-regen", action="store_true", help="Skip heuristic table regeneration.")

    args = parser.parse_args(argv)
    if args.command == "serve":
        if args.allow_public:
            os.environ["PIXELPIVOT_ALLOW_PUBLIC"] = "1"
        _run_serve(args.host, args.port)
    elif args.command == "convert":
        _run_convert(args.source, args.target, args.dry_run)
    elif args.command == "tui":
        _run_tui()
    elif args.command == "gui":
        _run_gui(args.port, args.api_url)
    elif args.command == "doctor":
        _run_doctor()
    elif args.command == "calibrate":
        _run_calibrate(args)


def _run_serve(host: str, port: int) -> None:
    import uvicorn
    os.environ["PIXELPIVOT_HOST"] = host
    uvicorn.run("app.batch_api.main:app", host=host, port=port)


def _run_convert(source: str, target: str, dry_run: bool) -> None:
    print("==================================================")
    print("      PixelPivot Environment Validation CLI       ")
    print("==================================================")
    
    validation_passed = True
    
    # 1. Check Paths
    if not check_paths(source, target):
        validation_passed = False
        
    # 2. Check Native Binaries
    ffmpeg_bin = str(PROJ_ROOT / "bin" / "ffmpeg" / "ffmpeg.exe")
    if not os.path.exists(ffmpeg_bin):
        alt_ffmpeg = str(PROJ_ROOT / "bin" / "ffmpeg" / "8.1.1-essentials_build" / "ffmpeg.exe")
        if os.path.exists(alt_ffmpeg):
            ffmpeg_bin = alt_ffmpeg
            
    magick_bin = str(PROJ_ROOT / "bin" / "magick" / "magick.exe")
    
    if not check_binary("FFmpeg", ffmpeg_bin):
        validation_passed = False
        
    if not check_binary("ImageMagick", magick_bin):
        validation_passed = False
        
    # 3. Check pyvips
    if not check_pyvips():
        validation_passed = False
        
    # 4. Check Sharp Installation & Daemon
    check_sharp_install()
    check_sharp_daemon()
    
    print("==================================================")
    if validation_passed:
        print(" Validation Result: PASSED")
        sys.exit(0)
    else:
        print(" Validation Result: FAILED")
        sys.exit(1)


def _run_tui() -> None:
    from app.tui.launcher import run_tui
    run_tui()


def _run_gui(port: int = 8503, api_url: str | None = None) -> None:
    # Streamlit ships only with the [gui] extra / embedded-Python deploy; it is
    # excluded from the frozen exe. Fail loudly with install guidance otherwise.
    try:
        import streamlit  # noqa: F401
    except ImportError:
        print(
            "Streamlit not installed. Install the GUI extra:\n"
            "    pip install pixelpivot-batch[gui]",
            file=sys.stderr,
        )
        sys.exit(1)

    if api_url:
        os.environ["BATCH_API_URL"] = api_url

    import subprocess

    main_py = str(PROJ_ROOT / "app" / "web" / "batch_gui" / "main.py")
    cmd = [
        sys.executable, "-m", "streamlit", "run", main_py,
        "--server.port", str(port),
    ]
    sys.exit(subprocess.call(cmd, cwd=str(PROJ_ROOT)))


def _run_calibrate(args) -> None:
    # Enable the calibration write gate live (config reads this attribute at call
    # time; setting the module attribute is robust regardless of import order).
    os.environ["PIXELPIVOT_CALIBRATION_ENABLED"] = "true"
    from app.core import config
    config.CALIBRATION_ENABLED = True

    from app.batch_api.calibration_runner import run_calibration

    def _split(s):
        return [x.strip() for x in s.split(",") if x.strip()]

    summary = run_calibration(
        args.source,
        _split(args.categories),
        _split(args.tools),
        _split(args.formats),
        sample=args.sample,
        target_ssim=args.target_ssim,
        regenerate_table=not args.no_regen,
    )
    print(
        f"Calibration run {summary['run_id']}: {summary['calibrated']} calibrated, "
        f"{summary['failures']} failed, across {summary['cells']} cells."
    )
    if summary.get("table"):
        print(f"Heuristic table regenerated: {summary['table']['heuristic_table']}")
    else:
        print("Heuristic table not regenerated.")


def _run_doctor() -> None:
    print("==================================================")
    print("      PixelPivot System Doctor / Validation       ")
    print("==================================================")
    
    validation_passed = True
    
    # 1. Check Native Binaries
    ffmpeg_bin = str(PROJ_ROOT / "bin" / "ffmpeg" / "ffmpeg.exe")
    if not os.path.exists(ffmpeg_bin):
        alt_ffmpeg = str(PROJ_ROOT / "bin" / "ffmpeg" / "8.1.1-essentials_build" / "ffmpeg.exe")
        if os.path.exists(alt_ffmpeg):
            ffmpeg_bin = alt_ffmpeg
            
    magick_bin = str(PROJ_ROOT / "bin" / "magick" / "magick.exe")
    
    if not check_binary("FFmpeg", ffmpeg_bin):
        validation_passed = False
        
    if not check_binary("ImageMagick", magick_bin):
        validation_passed = False
        
    # 2. Check pyvips
    if not check_pyvips():
        validation_passed = False
        
    # 3. Check Sharp Installation & Daemon
    check_sharp_install()
    check_sharp_daemon()
    
    print("==================================================")
    if validation_passed:
        print(" Doctor Result: PASSED")
        sys.exit(0)
    else:
        print(" Doctor Result: FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()

