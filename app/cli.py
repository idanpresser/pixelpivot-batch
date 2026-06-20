import argparse
import sys
import os
import shutil
import socket
from pathlib import Path

# Frozen-aware project root (PyInstaller bundles binaries next to the exe).
# Centralised in app.core.paths so the seam lives in exactly one place.
from app.core.paths import PROJ_ROOT
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

def main():
    parser = argparse.ArgumentParser(
        description="PixelPivot Batch Engine CLI tool for environment and path validation."
    )
    parser.add_argument(
        "--source", "-s",
        required=True,
        help="Path to the source directory containing images to convert."
    )
    parser.add_argument(
        "--target", "-t",
        required=True,
        help="Path to the target directory where output will be written."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform validation of environment, paths, and binaries without running conversion."
    )
    
    args = parser.parse_args()
    
    print("==================================================")
    print("      PixelPivot Environment Validation CLI       ")
    print("==================================================")
    
    validation_passed = True
    
    # 1. Check Paths
    if not check_paths(args.source, args.target):
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
        
    # 4. Check Sharp Daemon
    # Note: sharp daemon missing is a warning rather than hard failure since other tools can handle conversion.
    check_sharp_daemon()
    
    print("==================================================")
    if validation_passed:
        print(" Validation Result: PASSED")
        sys.exit(0)
    else:
        print(" Validation Result: FAILED")
        sys.exit(1)

if __name__ == "__main__":
    main()
