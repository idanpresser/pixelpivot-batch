"""Paths — centralized project and data directory resolution.

Resolves project root, app root, database paths, and dataset directories with
support for Docker environments and environment variable overrides.
"""

import os
import sys
from pathlib import Path

# 1. Resolve Project Root
_DEFAULT_APP_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_PROJ_ROOT = _DEFAULT_APP_ROOT.parent


def resolve_proj_root() -> Path:
    """Resolve the project root, frozen-aware for PyInstaller.

    Priority:
      1. ``PIXELPIVOT_PROJ_ROOT`` env override (any deployment).
      2. Frozen build (``sys.frozen``): the directory of the executable.
         PyInstaller ``--onedir`` ships native binaries (bin/, vendor/) next
         to the exe, so the source-tree ``__file__`` layout is meaningless.
      3. Source/dev run: two levels up from this file (``app/core`` -> root).
    """
    env_override = os.getenv("PIXELPIVOT_PROJ_ROOT")
    if env_override:
        return Path(env_override)
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        internal_dir = exe_dir / "_internal"
        if internal_dir.exists():
            return internal_dir
        return exe_dir
    return _DEFAULT_PROJ_ROOT


PROJ_ROOT = resolve_proj_root()
APP_ROOT = PROJ_ROOT / "app"


def resolve_data_dir() -> Path:
    """Resolve standard data directory (DB, logs, settings, heuristic adjustments).

    Priority:
      1. PIXELPIVOT_DATA_DIR env var if set.
      2. Frozen build on Windows: %ProgramData%/PixelPivot.
      3. Frozen build fallback: sys.executable directory / "data".
      4. Dev mode: PROJ_ROOT / "data".
    """
    data_env = os.getenv("PIXELPIVOT_DATA_DIR")
    if data_env:
        return Path(data_env)
    if getattr(sys, "frozen", False):
        pg_data = os.getenv("ProgramData")
        if pg_data and sys.platform == "win32":
            return Path(pg_data) / "PixelPivot"
        return Path(sys.executable).resolve().parent / "data"
    return PROJ_ROOT / "data"


# 2. Main Directories
TOOLS_DIR = PROJ_ROOT / "tools"

# 3. Docker Detection & DB Host Resolution
IS_DOCKER = os.path.exists("/.dockerenv") or os.getenv("IS_DOCKER") == "true"
DB_HOST = "db" if IS_DOCKER else "localhost"

# Post-Mortem Solution: Force IPv4 to bypass Docker/WSL2 IPv6 blackhole traps
if IS_DOCKER and DB_HOST == "db":
    try:
        import socket
        DB_HOST = socket.gethostbyname("db")
    except Exception:
        pass

# 4. SQLite Connection Config
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    abs_db_path = (resolve_data_dir() / "pixelpivot.db").absolute()
    SQLITE_DB_PATH = abs_db_path
    DATABASE_URL = f"sqlite:///{abs_db_path.as_posix()}"
else:
    # If DATABASE_URL is provided, extract the path for SQLITE_DB_PATH
    if DATABASE_URL.startswith("sqlite:///"):
        SQLITE_DB_PATH = Path(DATABASE_URL.replace("sqlite:///", ""))
    else:
        SQLITE_DB_PATH = resolve_data_dir() / "pixelpivot.db"

# Dataset paths
DATASET_DIR = Path(os.getenv("PIXELPIVOT_DATASET_DIR", PROJ_ROOT / "dataset"))
