"""Structured (print-free) tool availability probes.

Shared by the CLI (which formats output) and the TUI Tools screen (which renders
a status board). Mirrors the legacy check_* helpers in app/cli.py but returns
data instead of printing.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ToolStatus:
    name: str
    ok: bool
    version: Optional[str] = None
    detail: Optional[str] = None


def check_binary(name: str, path_str: str) -> ToolStatus:
    """Check a binary at an explicit path, falling back to PATH lookup."""
    resolved = path_str if os.path.exists(path_str) else shutil.which(name)
    if not resolved:
        return ToolStatus(name, ok=False, detail="not found")
    version = None
    try:
        out = subprocess.run([resolved, "--version"], capture_output=True,
                             text=True, timeout=5)
        version = (out.stdout or out.stderr).splitlines()[0].strip() if (out.stdout or out.stderr) else None
    except Exception:
        version = None
    return ToolStatus(name, ok=True, version=version, detail=resolved)


def check_pyvips() -> ToolStatus:
    """Check that pyvips/libvips imports and its native library loads."""
    try:
        import pyvips
        ver = f"{pyvips.version(0)}.{pyvips.version(1)}.{pyvips.version(2)}"
        return ToolStatus("vips", ok=True, version=ver)
    except Exception as e:
        return ToolStatus("vips", ok=False, detail=str(e))


def check_sharp_daemon(port: int = 8765, timeout: float = 1.0) -> ToolStatus:
    """Check whether the sharp daemon is accepting connections on its port."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return ToolStatus("sharp", ok=True, detail=f"listening :{port}")
    except Exception as e:
        return ToolStatus("sharp", ok=False, detail=f"down ({e})")


def check_all(ffmpeg_path: str, magick_path: str, sharp_port: int = 8765) -> list[ToolStatus]:
    """Probe all four tools and return their statuses in display order."""
    return [
        check_binary("magick", magick_path),
        check_binary("ffmpeg", ffmpeg_path),
        check_pyvips(),
        check_sharp_daemon(sharp_port),
    ]
