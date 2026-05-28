"""
Stderr-line classification for the FFmpeg subprocess wrapper.

The fatal-marker list lives in app/core/config.py — this module is just the
classifier function that reads it. Keep all tunable lists in config.
"""

from __future__ import annotations

from typing import Literal

from ..config import FFMPEG_FATAL_MARKERS

Severity = Literal["info", "warning", "error", "fatal"]


def classify_stderr_line(line: str) -> Severity:
    """Classify an FFmpeg stderr line by severity.

    Detects fatal errors (command/binary not found, unrecoverable codec issues,
    etc.), general errors, warnings, and info messages. Fatal markers are
    defined in config.FFMPEG_FATAL_MARKERS.

    Args:
        line: A single line of FFmpeg stderr output.

    Returns:
        Severity literal: 'fatal', 'error', 'warning', or 'info'.
    """
    low = line.lower()

    # Narrow down general 'not found' checks to prevent false-positives on fonts/profiles
    if "not found" in low and any(kw in low for kw in ("command", "binary", "executable", "magick", "ffmpeg")):
        return "fatal"

    # Specific fatal markers from config.py
    for marker in FFMPEG_FATAL_MARKERS:
        # Special case: don't let "(av1_nvenc)" mapping line trip "nvenc" markers
        if "nvenc" in marker and "->" in low and "(" in low:
            continue

        if marker in low:
            return "fatal"

    if "error" in low or "failed" in low:
        return "error"
    if "warning" in low or "deprecated" in low:
        return "warning"
    return "info"
