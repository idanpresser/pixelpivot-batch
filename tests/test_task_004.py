import pytest
from pydantic import ValidationError
from app.batch_api.models import BatchRequest, HotFolderRequest, Tool

def test_batch_request_rejects_empty_matrix_lists():
    """
    Regression test for Task 004: BatchRequest should reject empty lists
    for target_format, tool, or category.
    """
    # Happy path works
    req = BatchRequest(
        source_dir="src",
        target_dir="tgt",
        target_format=["webp"],
        tool=[Tool.magick],
        category=["highRes"]
    )
    assert len(req.target_format) == 1

    # Empty target_format fails
    with pytest.raises(ValidationError):
        BatchRequest(
            source_dir="src",
            target_dir="tgt",
            target_format=[],
            tool=[Tool.magick],
            category=["highRes"]
        )

    # Empty tool fails
    with pytest.raises(ValidationError):
        BatchRequest(
            source_dir="src",
            target_dir="tgt",
            target_format=["webp"],
            tool=[],
            category=["highRes"]
        )

    # Empty category fails
    with pytest.raises(ValidationError):
        BatchRequest(
            source_dir="src",
            target_dir="tgt",
            target_format=["webp"],
            tool=[Tool.magick],
            category=[]
        )

def test_hot_folder_request_rejects_empty_matrix_lists():
    """
    Regression test for Task 004: HotFolderRequest should reject empty lists
    for target_format, tool, or category.
    """
    with pytest.raises(ValidationError):
        HotFolderRequest(
            source_dir="src",
            target_dir="tgt",
            target_format=[],
            tool=[Tool.magick],
            category=["highRes"]
        )
