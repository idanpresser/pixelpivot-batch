#!/usr/bin/env python3
"""Download and set up self-contained Linux binaries for PixelPivot.

Downloads FFmpeg static builds and extracts ImageMagick AppImage to package
cross-platform Linux binaries in bin/ ffmpeg and magick folders.
"""
import os
import sys
import shutil
import urllib.request
import json
import tarfile
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = PROJECT_ROOT / "bin"
FFMPEG_DIR = BIN_DIR / "ffmpeg"
MAGICK_DIR = BIN_DIR / "magick"
TEMP_DIR = PROJECT_ROOT / "scratch" / "temp_binaries"

FFMPEG_URL = "https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz"

def download_file(url: str, dest_path: Path):
    """Download a file with progress indicators."""
    print(f"📥 Downloading {url}...")
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        with open(dest_path, 'wb') as f:
            shutil.copyfileobj(response, f)
    print(f"✅ Saved to {dest_path}")

def get_latest_imagemagick_url() -> str:
    """Query GitHub API to find the latest ImageMagick GCC AppImage URL."""
    print("🔍 Querying GitHub for the latest ImageMagick AppImage...")
    url = "https://api.github.com/repos/ImageMagick/ImageMagick/releases/latest"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            for asset in data.get('assets', []):
                name = asset.get('name', '')
                if name.endswith('.AppImage') and 'gcc' in name and 'x86_64' in name:
                    return asset.get('browser_download_url')
            # fallback to any AppImage if gcc isn't found
            for asset in data.get('assets', []):
                name = asset.get('name', '')
                if name.endswith('.AppImage') and 'x86_64' in name:
                    return asset.get('browser_download_url')
    except Exception as e:
        print(f"⚠️ Error querying GitHub: {e}")
    # Hard fallback URL if GitHub API fails
    return "https://github.com/ImageMagick/ImageMagick/releases/download/7.1.2-26/ImageMagick-7.1.2-26-gcc-x86_64.AppImage"

def setup_ffmpeg():
    """Download, extract, and copy FFmpeg/FFprobe binaries."""
    print("\n--- Setting up FFmpeg & FFprobe (Linux) ---")
    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    
    tar_path = TEMP_DIR / "ffmpeg.tar.xz"
    download_file(FFMPEG_URL, tar_path)
    
    print("📦 Extracting FFmpeg archive...")
    with tarfile.open(tar_path, "r:xz") as tar:
        # Find the directories in the tar file
        members = tar.getmembers()
        # Find ffmpeg and ffprobe files
        ffmpeg_member = None
        ffprobe_member = None
        for m in members:
            if m.name.endswith("/ffmpeg") and m.isreg():
                ffmpeg_member = m
            elif m.name.endswith("/ffprobe") and m.isreg():
                ffprobe_member = m
                
        if not ffmpeg_member or not ffprobe_member:
            raise RuntimeError("Could not find ffmpeg or ffprobe in the archive")
            
        # Extract files
        ffmpeg_dest = FFMPEG_DIR / "ffmpeg"
        ffprobe_dest = FFMPEG_DIR / "ffprobe"
        
        # We extract them manually to FFMPEG_DIR
        with tar.extractfile(ffmpeg_member) as src, open(ffmpeg_dest, 'wb') as dst:
            shutil.copyfileobj(src, dst)
        with tar.extractfile(ffprobe_member) as src, open(ffprobe_dest, 'wb') as dst:
            shutil.copyfileobj(src, dst)
            
    ffmpeg_dest.chmod(0o755)
    ffprobe_dest.chmod(0o755)
    print(f"🚀 FFmpeg set up at {ffmpeg_dest}")
    print(f"🚀 FFprobe set up at {ffprobe_dest}")

def setup_imagemagick():
    """Download and extract ImageMagick AppImage, configuring wrapping script."""
    print("\n--- Setting up ImageMagick (Linux) ---")
    
    # 1. Clean old magick dir if it exists to avoid library mismatch
    if MAGICK_DIR.exists():
        print(f"🧹 Cleaning existing Magick directory: {MAGICK_DIR}")
        shutil.rmtree(MAGICK_DIR)
    MAGICK_DIR.mkdir(parents=True, exist_ok=True)
    
    appimage_url = get_latest_imagemagick_url()
    appimage_path = TEMP_DIR / "ImageMagick.AppImage"
    download_file(appimage_url, appimage_path)
    
    appimage_path.chmod(0o755)
    
    # 2. Extract AppImage
    print("📦 Extracting ImageMagick AppImage (no-FUSE bypass)...")
    extract_dir = TEMP_DIR / "squashfs-root"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
        
    subprocess.run(
        [str(appimage_path), "--appimage-extract"],
        cwd=str(TEMP_DIR),
        check=True,
        stdout=subprocess.DEVNULL
    )
    
    # 3. Rename AppRun to magick inside squashfs-root
    apprun_src = extract_dir / "AppRun"
    magick_script_src = extract_dir / "magick"
    if apprun_src.exists():
        apprun_src.rename(magick_script_src)
    magick_script_src.chmod(0o755)
    
    # 4. Copy extracted content to MAGICK_DIR
    print("📂 Staging ImageMagick files into bin/magick...")
    for item in extract_dir.iterdir():
        dest = MAGICK_DIR / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dest)
            
    print(f"🚀 ImageMagick set up at {MAGICK_DIR / 'magick'}")

def main():
    if sys.platform == "win32":
        print("❌ Error: This script is intended to download Linux binaries, but you are running on Windows.")
        print("For Windows, binaries are already pre-packaged in the repo or download scripts are in PowerShell.")
        sys.exit(1)
        
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        setup_ffmpeg()
        setup_imagemagick()
        print("\n🎉 Linux binaries setup complete!")
    except Exception as e:
        print(f"\n❌ Setup failed: {e}")
        sys.exit(1)
    finally:
        # Cleanup temp directory
        if TEMP_DIR.exists():
            print("🧹 Cleaning up temporary files...")
            shutil.rmtree(TEMP_DIR)

if __name__ == "__main__":
    main()
