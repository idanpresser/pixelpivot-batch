# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules
from pathlib import Path
import os

block_cipher = None

# Base directories
project_root = Path('.')

# Collect DLLs under bin/vips/ recursively for 'binaries'
vips_dlls = []
vips_dir = project_root / 'bin' / 'vips'
if vips_dir.exists():
    for f in vips_dir.rglob('*.dll'):
        # Target destination directory inside the build must match the relative path
        rel_parent = f.parent.relative_to(project_root)
        vips_dlls.append((str(f), str(rel_parent)))

# Collect non-DLL files under bin/vips/ for 'datas'
vips_datas = []
if vips_dir.exists():
    for f in vips_dir.rglob('*'):
        if f.is_file() and f.suffix.lower() != '.dll':
            rel_parent = f.parent.relative_to(project_root)
            vips_datas.append((str(f), str(rel_parent)))

datas = [
    ('app/core/heuristic_table.json', 'app/core'),
    ('app/core/heuristic_weights.json', 'app/core'),
    ('services/sharp-daemon/package.json', 'services/sharp-daemon'),
    ('services/sharp-daemon/package-lock.json', 'services/sharp-daemon'),
    ('services/sharp-daemon/sharp_daemon.js', 'services/sharp-daemon'),
    ('vendor/node', 'node'),
    ('bin/ffmpeg', 'bin/ffmpeg'),
    ('bin/magick', 'bin/magick'),
] + vips_datas

# Dynamically map node_modules to services/sharp-daemon/node_modules
if Path('vendor/node/node_modules').exists():
    datas.append(('vendor/node/node_modules', 'services/sharp-daemon/node_modules'))
elif Path('services/sharp-daemon/node_modules').exists():
    datas.append(('services/sharp-daemon/node_modules', 'services/sharp-daemon/node_modules'))

# Map sharp node module to services/sharp-daemon/node_modules/sharp in the bundle
if Path('services/sharp-daemon/node_modules/sharp').exists():
    datas.append(('services/sharp-daemon/node_modules/sharp', 'services/sharp-daemon/node_modules/sharp'))
elif Path('vendor/node/node_modules/sharp').exists():
    datas.append(('vendor/node/node_modules/sharp', 'services/sharp-daemon/node_modules/sharp'))

# Hidden imports
hiddenimports = (
    collect_submodules('uvicorn') +
    collect_submodules('app.batch_api') +
    collect_submodules('app.core') +
    collect_submodules('app.tui') +
    [
        'pyvips',
        'rich',
        'prompt_toolkit',
    ]
)

excludes = [
    'streamlit',
    'app.web',
    'app.web.batch_gui',
    'matplotlib',
    'wand'
]

a = Analysis(
    ['app/cli.py'],
    pathex=[],
    binaries=vips_dlls,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='pixelpivot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='pixelpivot',
)
