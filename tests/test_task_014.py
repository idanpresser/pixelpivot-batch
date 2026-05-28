"""Task 014 - symmetric target_format validation across request models.

BatchRequest.target_format accepted any string (List[str]), so
POST /batch/start with target_format=["garbage"] passed validation and only
failed later, per-file, deep in the converters. HotFolderRequest already
restricted it to the TargetFormat Literal ("webp"/"avif"/"jxl"). Both entry
points should reject unknown formats at the schema boundary.
"""

import pytest
from pydantic import ValidationError

from app.batch_api.models import BatchRequest


def test_batch_request_rejects_unknown_format():
    with pytest.raises(ValidationError):
        BatchRequest(
            source_dir="src",
            target_dir="dst",
            target_format=["garbage"],
            tool=["magick"],
            category=["general"],
        )


def test_batch_request_accepts_valid_formats():
    req = BatchRequest(
        source_dir="src",
        target_dir="dst",
        target_format=["webp", "avif", "jxl"],
        tool=["magick"],
        category=["general"],
    )
    assert req.target_format == ["webp", "avif", "jxl"]


def test_batch_request_still_rejects_empty_format_list():
    # Preserves the min_length=1 rule from task_004.
    with pytest.raises(ValidationError):
        BatchRequest(
            source_dir="src",
            target_dir="dst",
            target_format=[],
            tool=["magick"],
            category=["general"],
        )
