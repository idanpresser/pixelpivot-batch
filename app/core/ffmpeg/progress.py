"""
FFmpeg -progress stream parser.

FFmpeg's `-progress URL` flag emits line-oriented key=value pairs separated by
`progress=continue` (mid-stream) or `progress=end` (final) terminator lines:

    frame=12
    fps=8.42
    out_time_us=400000
    total_size=51200
    bitrate=20.5kbits/s
    speed=0.34x
    progress=continue
    ...

For still-image encodes this typically fires once or twice — useful as a
heartbeat for stall detection and for capturing final stats, not as a
percentage UI signal.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgressSample:
    """A single progress event from FFmpeg -progress pipe:1.

    Emitted once or twice per still-image encode; useful as a heartbeat
    for stall detection and capturing final encoding stats.

    Attributes:
        frame: Frame number (0 or 1 for still images).
        fps: Frames per second (encoding speed).
        out_time_us: Output timestamp in microseconds.
        total_size: Output size in bytes.
        bitrate_kbps: Bitrate in kilobits per second.
        speed: Encoding speed relative to real-time (e.g., 0.34x).
        done: True if this is the final progress=end marker.
        wall_ms: Wall-clock time in milliseconds since ProgressParser creation.
    """
    frame: int
    fps: float
    out_time_us: int
    total_size: int
    bitrate_kbps: float
    speed: float
    done: bool
    wall_ms: float


def _parse_float(raw: str) -> float:
    """Parse a float value from FFmpeg progress output.

    Handles 'N/A', suffixes like '20.5kbits/s' or '0.34x', and missing values.

    Args:
        raw: The value string from FFmpeg.

    Returns:
        Parsed float, or 0.0 if parsing fails.
    """
    if not raw or raw == "N/A":
        return 0.0
    cleaned = raw.rstrip("x").rstrip("%")
    for suffix in ("kbits/s", "bits/s", "kb/s", "mb/s"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
            break
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _parse_int(raw: str) -> int:
    """Parse an integer value from FFmpeg progress output.

    Handles 'N/A', float strings, and missing values.

    Args:
        raw: The value string from FFmpeg.

    Returns:
        Parsed integer, or 0 if parsing fails.
    """
    if not raw or raw == "N/A":
        return 0
    try:
        return int(raw)
    except ValueError:
        try:
            return int(float(raw))
        except ValueError:
            return 0


class ProgressParser:
    """Parse FFmpeg -progress pipe:1 output into ProgressSample events.

    Accumulates key=value lines and emits a ProgressSample when a terminator
    (progress=continue or progress=end) arrives. Caller is responsible for
    splitting bytes into lines; this class accepts already-stripped strings.
    """

    def __init__(self) -> None:
        """Initialize the parser."""
        self._buf: dict[str, str] = {}
        self._start = time.monotonic()

    def feed_line(self, line: str) -> ProgressSample | None:
        """Feed a single line of progress output.

        Args:
            line: A stripped line from FFmpeg stdout.

        Returns:
            ProgressSample when a progress terminator arrives, None while accumulating.
        """
        line = line.strip()
        if not line:
            return None
        if "=" not in line:
            return None

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        if key != "progress":
            self._buf[key] = value
            return None

        sample = self._emit(done=(value == "end"))
        self._buf.clear()
        return sample

    def _emit(self, *, done: bool) -> ProgressSample:
        """Construct a ProgressSample from accumulated key-value pairs.

        Args:
            done: True if this is the final progress=end marker.

        Returns:
            A new ProgressSample instance.
        """
        buf = self._buf
        return ProgressSample(
            frame=_parse_int(buf.get("frame", "0")),
            fps=_parse_float(buf.get("fps", "0")),
            out_time_us=_parse_int(buf.get("out_time_us", "0")),
            total_size=_parse_int(buf.get("total_size", "0")),
            bitrate_kbps=_parse_float(buf.get("bitrate", "0")),
            speed=_parse_float(buf.get("speed", "0")),
            done=done,
            wall_ms=(time.monotonic() - self._start) * 1000.0,
        )
