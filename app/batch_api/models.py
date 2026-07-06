"""Pydantic request/response schemas for batch API endpoints.

Defines validation rules for batch jobs and hot folder registration.
"""
from enum import Enum
from pathlib import Path
from typing import Literal, Optional, List, Annotated
from datetime import datetime
from pydantic import BaseModel, field_validator, Field

class Tool(str, Enum):
    """Supported image conversion tools."""
    magick = "magick"
    ffmpeg = "ffmpeg"
    vips = "vips"
    sharp = "sharp"
    cavif = "cavif"

TargetFormat = Literal["webp", "avif", "jxl"]

def _resolve_path(v: str) -> str:
    """Validate and resolve a filesystem path.

    Converts relative paths to absolute, enforces non-empty, and optionally
    checks containment within PIXELPIVOT_ALLOWED_ROOT if set.

    Args:
        v: Path string to validate.

    Returns:
        Absolute path as string.

    Raises:
        ValueError: On empty, invalid, or escaped paths.
    """
    import os
    if not v or not v.strip():
        raise ValueError("Path must not be empty.")
    try:
        normalized = v.replace('\\', '/')
        if normalized.startswith('~'):
            expanded = os.path.expanduser(normalized)
            if not os.path.exists(expanded):
                home_fallback = normalized.replace('~', '/home', 1)
                if os.path.exists(home_fallback):
                    expanded = home_fallback
            normalized = expanded
            
        resolved = Path(normalized).resolve()

        # Optional containment check
        allowed_root = os.environ.get("PIXELPIVOT_ALLOWED_ROOT")
        if allowed_root:
            base = Path(allowed_root).resolve()
            if base not in resolved.parents and resolved != base:
                raise ValueError("Path escapes the allowed root.")

        return str(resolved)
    except (ValueError, OSError) as e:
        raise ValueError(f"Invalid path: {e}")


class BatchRequest(BaseModel):
    """Request schema for triggering a batch conversion job.

    Validates that source and target directories exist and are accessible,
    and that at least one format, tool, and category are specified.
    """
    source_dir: str
    target_dir: str
    target_format: Annotated[List[TargetFormat], Field(min_length=1)]
    tool: Annotated[List[Tool], Field(min_length=1)]
    category: Annotated[List[str], Field(min_length=1)] = ["general"]
    trigger_type: str = "manual"
    input_files: Optional[List[str]] = None
    sample: Optional[int] = None

    @field_validator("source_dir", "target_dir")
    @classmethod
    def resolve_path(cls, v: str) -> str:
        return _resolve_path(v)

    @field_validator("sample")
    @classmethod
    def validate_sample(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 1:
            raise ValueError("sample must be a positive integer greater than 1")
        return v


class CalibrationRequest(BaseModel):
    """Request schema for an offline serial calibration run."""
    source_dir: str
    target_format: Annotated[List[TargetFormat], Field(min_length=1)]
    tool: Annotated[List[Tool], Field(min_length=1)]
    category: Annotated[List[str], Field(min_length=1)] = ["general"]
    sample: int = 30
    target_ssim: float = 0.98
    regenerate_table: bool = True

    @field_validator("source_dir")
    @classmethod
    def resolve_path(cls, v: str) -> str:
        return _resolve_path(v)

    @field_validator("sample")
    @classmethod
    def validate_sample(cls, v: int) -> int:
        if v <= 1:
            raise ValueError("sample must be a positive integer greater than 1")
        return v


class HotFolderRequest(BaseModel):
    """Request schema for registering a hot folder watcher.

    Validates directories and conversion parameters for automatic monitoring.
    """
    source_dir: str
    target_dir: str
    target_format: Annotated[List[TargetFormat], Field(min_length=1)]
    tool: Annotated[List[Tool], Field(min_length=1)]
    category: Annotated[List[str], Field(min_length=1)] = ["general"]

    @field_validator("source_dir", "target_dir")
    @classmethod
    def resolve_path(cls, v: str) -> str:
        return _resolve_path(v)

class BatchStatusResponse(BaseModel):
    """Response schema for batch job status queries.

    Includes aggregated metrics (summary) only when the batch has completed.
    """
    run_id: int
    status: str
    total_images: int
    created_at: datetime
    completed_at: Optional[datetime] = None
    summary: Optional[dict] = None


class ControlRequest(BaseModel):
    """Request schema for controlling an in-flight batch run."""
    action: Literal["pause", "resume", "stop"]

