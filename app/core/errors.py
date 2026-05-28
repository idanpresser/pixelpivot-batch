"""Errors — categorized exception types for image processing pipeline failures."""

from enum import Enum
from dataclasses import dataclass
from typing import Optional


class ErrorCategory(Enum):
    """Pipeline error categories for classification and recovery decisions."""

    GPU_OOM = "gpu_oom"
    TIMEOUT = "timeout"
    CONVERTER_UNSUPPORTED = "converter_unsupported"
    CORRUPT_OUTPUT = "corrupt_output"
    SYSTEM_IO = "system_io"
    UNKNOWN = "unknown"


@dataclass
class PipelineError(Exception):
    """Base exception for image processing pipeline errors."""
    message: str
    category: ErrorCategory
    recoverable: bool = False
    original_exception: Optional[Exception] = None

    def __str__(self):
        return f"[{self.category.value.upper()}] {self.message}"


class GPUOutOfMemoryError(PipelineError):
    """GPU memory exhaustion error (typically unrecoverable)."""

    def __init__(self, message="GPU Out of Memory", original_exception=None):
        super().__init__(
            message,
            ErrorCategory.GPU_OOM,
            recoverable=False,
            original_exception=original_exception,
        )


class ConversionTimeoutError(PipelineError):
    """Conversion process exceeded wall-clock timeout (typically recoverable via retry)."""

    def __init__(self, message="Conversion process timed out", original_exception=None):
        super().__init__(
            message,
            ErrorCategory.TIMEOUT,
            recoverable=True,
            original_exception=original_exception,
        )


class ConverterUnsupportedError(PipelineError):
    """Tool/format combination is not supported (unrecoverable)."""

    def __init__(
        self,
        message="Converter does not support this operation",
        original_exception=None,
    ):
        super().__init__(
            message,
            ErrorCategory.CONVERTER_UNSUPPORTED,
            recoverable=False,
            original_exception=original_exception,
        )


class CorruptOutputError(PipelineError):
    """Output file is empty or unreadable (typically recoverable via retry)."""

    def __init__(
        self, message="Output file was empty or corrupted", original_exception=None
    ):
        super().__init__(
            message,
            ErrorCategory.CORRUPT_OUTPUT,
            recoverable=True,
            original_exception=original_exception,
        )
