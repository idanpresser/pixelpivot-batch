# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for PixelPivotService.exe
#
# Bundles the Windows service wrapper, FastAPI/uvicorn backend, and Streamlit
# GUI into a single --onedir executable.  The exe dispatches on argv:
#   --mode api   → uvicorn (child spawned by service)
#   --mode gui   → streamlit (child spawned by service)
#   bare         → SCM commands (install / start / stop / remove)

from PyInstaller.utils.hooks import collect_submodules, collect_all
from pathlib import Path

block_cipher = None
project_root = Path(".")

# ── VIPS binaries and data files ──────────────────────────────────────────
vips_dlls, vips_datas = [], []
vips_dir = project_root / "bin" / "vips"
if vips_dir.exists():
    for f in vips_dir.rglob("*.dll"):
        rel = f.parent.relative_to(project_root)
        vips_dlls.append((str(f), str(rel)))
    for f in vips_dir.rglob("*"):
        if f.is_file() and f.suffix.lower() != ".dll":
            rel = f.parent.relative_to(project_root)
            vips_datas.append((str(f), str(rel)))

# ── Streamlit (data files + hidden imports) ────────────────────────────────
st_datas, st_binaries, st_hidden = collect_all("streamlit")
altair_datas, altair_binaries, altair_hidden = collect_all("altair")

# ── Assembled datas ────────────────────────────────────────────────────────
datas = [
    ("app/core/heuristic_table.json",            "app/core"),
    ("app/core/heuristic_weights.json",           "app/core"),
    ("services/sharp-daemon/package.json",         "services/sharp-daemon"),
    ("services/sharp-daemon/package-lock.json",    "services/sharp-daemon"),
    ("services/sharp-daemon/sharp_daemon.js",      "services/sharp-daemon"),
    ("vendor/node",                                "node"),
    ("bin/ffmpeg",                                 "bin/ffmpeg"),
    ("bin/magick",                                 "bin/magick"),
    # Physical .py file — streamlit runner requires a real path, not a .pyc
    ("app/web/batch_gui/main.py",                  "app/web/batch_gui"),
    ("app/web/batch_gui/gui_defaults.json",        "app/web/batch_gui"),
    ("app/web/batch_gui/static",                   "app/web/batch_gui/static"),
    (".streamlit/config.toml",                     ".streamlit"),
] + vips_datas + st_datas + altair_datas

if Path("vendor/node/node_modules").exists():
    datas.append(("vendor/node/node_modules", "services/sharp-daemon/node_modules"))
elif Path("services/sharp-daemon/node_modules").exists():
    datas.append(("services/sharp-daemon/node_modules", "services/sharp-daemon/node_modules"))

if Path("vendor/node/node_modules/sharp").exists():
    datas.append(("vendor/node/node_modules/sharp", "services/sharp-daemon/node_modules/sharp"))
elif Path("services/sharp-daemon/node_modules/sharp").exists():
    datas.append(("services/sharp-daemon/node_modules/sharp", "services/sharp-daemon/node_modules/sharp"))

# ── Hidden imports ─────────────────────────────────────────────────────────
hiddenimports = (
    collect_submodules("uvicorn")       +
    collect_submodules("app.batch_api") +
    collect_submodules("app.core")      +
    collect_submodules("app.web")       +
    collect_submodules("app.windows")   +
    st_hidden + altair_hidden           +
    [
        "pyvips",
        "win32service",
        "win32serviceutil",
        "win32event",
        "servicemanager",
        "win32api",
        "win32con",
        "pywintypes",
    ]
)

a = Analysis(
    ["app/windows/service_main.py"],
    pathex=[],
    binaries=vips_dlls + st_binaries + altair_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PySide6", "PyQt5", "PyQt6", "tkinter", "matplotlib"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PixelPivotService",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    uac_admin=True,   # SCM registration requires admin; SYSTEM children are unaffected
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="pixelpivot_service",
)
