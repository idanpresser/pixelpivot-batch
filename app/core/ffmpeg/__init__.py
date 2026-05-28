"""FFmpeg subprocess wrapper with progress tracking, timeout supervision, and fatal-error detection."""

from .errors import Severity, classify_stderr_line
from .process import FFmpegProcess, FFmpegResult
from .progress import ProgressParser, ProgressSample

__all__ = [
    "FFmpegProcess",
    "FFmpegResult",
    "ProgressParser",
    "ProgressSample",
    "Severity",
    "classify_stderr_line",
]
