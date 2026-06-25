"""Utils — shared image utilities for dimension probing, path resolution, and quality mapping.

Core utilities: pyvips initialization, ffprobe/PIL image probing, resolution bucketing,
and JXL Butteraugli distance mapping.
"""

import os
import sys
from typing import Union
import json
import subprocess
import psutil
from .logger import get_logger

log = get_logger(__name__)


def kill_process_tree(pid: int, timeout_s: float = 3.0):
    """Forcefully terminate a process and all its children.

    Args:
        pid: Process ID to kill.
        timeout_s: Seconds to wait before force-killing survivors.
    """
    try:
        parent = psutil.Process(pid)
        children = parent.children(recursive=True)

        # Terminate parent
        parent.terminate()

        # Terminate children
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                pass

        # Wait for termination
        gone, alive = psutil.wait_procs(children + [parent], timeout=timeout_s)

        # Kill survivors
        for p in alive:
            try:
                log.warning(f"Process {p.pid} survived terminate(), killing.")
                p.kill()
            except psutil.NoSuchProcess:
                pass

    except psutil.NoSuchProcess:
        pass
    except Exception as e:
        log.error(f"Error killing process tree for {pid}: {e}")


def ensure_vips_dlls():
    """Ensure libvips DLLs are in PATH on Windows by searching common locations."""
    if sys.platform == "win32":
        from .paths import PROJ_ROOT

        # Search for libvips in bin/ or vendor/ (e.g. vips-dev-8.15)
        search_dirs = [PROJ_ROOT / "bin", PROJ_ROOT / "vendor"]
        vips_bin = None

        for d in search_dirs:
            if not d.exists(): continue
            # Look for a directory named 'vips' or starting with 'vips-dev-'
            candidates = list(d.glob("vips")) + list(d.glob("vips-dev-*"))
            if candidates:
                # Use the first one found
                vips_bin = candidates[0] / "bin"
                break

        if vips_bin and vips_bin.exists():
            vips_path = str(vips_bin.absolute())
            log.info(f"Adding libvips to DLL search path: {vips_path}")

            # Safe and duplicate-protected DLL search directory addition
            if hasattr(os, "add_dll_directory"):
                try:
                    os.add_dll_directory(vips_path)
                except Exception as dll_err:
                    log.warning(f"Failed to add DLL directory {vips_path} via os.add_dll_directory: {dll_err}")

            # Exact path-element matching to avoid false substring matching in PATH check
            current_path = os.environ.get("PATH", "")
            path_elements = [os.path.normpath(p.strip()) for p in current_path.split(os.pathsep) if p.strip()]
            norm_vips_path = os.path.normpath(vips_path)

            if norm_vips_path not in path_elements:
                os.environ["PATH"] = vips_path + os.pathsep + current_path


import threading

# Lazy pyvips initialization to avoid premature side effects during test collection
_pyvips_cache = None
_VIPS_AVAILABLE = None
_vips_lock = threading.Lock()

def _init_vips():
    """Initialize pyvips once at first access (thread-safe lazy loading)."""
    global _pyvips_cache, _VIPS_AVAILABLE
    with _vips_lock:
        if _VIPS_AVAILABLE is not None:
            return

        try:
            ensure_vips_dlls()
            import pyvips
            _pyvips_cache = pyvips
            _VIPS_AVAILABLE = True
            log.debug("pyvips initialized successfully.")
        except (ImportError, OSError, RuntimeError) as e:
            log.error(f"pyvips initialization failed (falling back to PIL): {e}")
            _VIPS_AVAILABLE = False


def is_vips_available() -> bool:
    """Check whether pyvips was successfully initialized.

    Returns:
        True if pyvips is available and can be used.
    """
    if _VIPS_AVAILABLE is None:
        _init_vips()
    return _VIPS_AVAILABLE


def get_pyvips():
    """Lazy accessor for the pyvips module.

    Returns:
        pyvips module if available, else None.
    """
    if _VIPS_AVAILABLE is None:
        _init_vips()
    return _pyvips_cache


def vips_has_loader(loader_name: str) -> bool:
    """Check if the current libvips has a specific loader installed.

    Args:
        loader_name: Loader name (e.g. 'jxlload', 'heifload').

    Returns:
        True if the loader is available, False otherwise.
    """
    if not is_vips_available():
        return False
    try:
        # vips_type_find returns 0 if not found
        return get_pyvips().type_find("VipsForeignLoad", loader_name) != 0
    except Exception:
        return False


def probe_image_dimensions(path: str) -> tuple[int, int]:
    """Probe image dimensions using ffprobe, falling back to PIL.

    Args:
        path: Path to image file.

    Returns:
        (width, height) tuple in pixels.
    """
    from .converters.base import _win32_safe_path
    safe = _win32_safe_path(path)
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        safe,
    ]
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            timeout=5.0,
            creationflags=creationflags,
        )
        data = json.loads(result.stdout)
        stream = data["streams"][0]
        return int(stream["width"]), int(stream["height"])
    except (subprocess.SubprocessError, FileNotFoundError, KeyError, IndexError, json.JSONDecodeError) as e:
        log.debug(f"ffprobe failed for {path} ({e}), falling back to PIL.")
        from PIL import Image
        with Image.open(path) as img:
            return img.size


def get_resolution_bucket(width: int, height: int) -> str:
    """Categorize an image into standard resolution buckets for heuristic lookup.

    Args:
        width: Image width in pixels.
        height: Image height in pixels.

    Returns:
        Bucket name ("small", "medium", "large", "xlarge", or "unknown").
    """
    if not width or not height:
        return "unknown"
    megapixels = (width * height) / 1_000_000.0

    if megapixels < 0.5:
        return "small"
    elif megapixels < 2.0:
        return "medium"
    elif megapixels <= 8.0:
        return "large"
    else:
        return "xlarge"


def get_resolution_bucket_from_path(path: str) -> str:
    """Get the resolution bucket for a file on disk.

    Args:
        path: Path to image file.

    Returns:
        Bucket name, or "unknown" if probing fails.
    """
    try:
        w, h = probe_image_dimensions(path)
        return get_resolution_bucket(w, h)
    except Exception:
        return "unknown"


def quality_to_jxl_distance(quality: Union[int, float]) -> float:
    """Map 0-100 quality scale to JXL Butteraugli distance (0.0-15.0).

    Small distance indicates high quality. Implements the formula:
    distance = (100 - quality) / 10.0, clamped to 15.0.

    Args:
        quality: Quality scalar in 0-100 range.

    Returns:
        Butteraugli distance (0.0-15.0).
    """
    if quality >= 100:
        return 0.0
    # Formula: distance = (100 - quality) / 10.0
    dist = (100.0 - float(quality)) / 10.0
    return round(min(15.0, dist), 2)


def cast_quality(target_format: str, quality: Union[int, float]) -> float:
    """Apply canonical rounding for heuristic table cell values.

    Single source of truth for both generators. Format-aware for possible future
    per-format rules, but currently uniform: round to 2 decimals. Never int-cast
    because interpolation needs the fractional part.

    Args:
        target_format: Target format (kept for extensibility).
        quality: Quality scalar to round.

    Returns:
        Rounded quality as float.
    """
    return round(float(quality), 2)
