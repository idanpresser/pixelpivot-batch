import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest
from app.core.config import MASSIVE_IMAGE_THRESHOLD

def test_massive_image_upfront_rejection(tmp_path, monkeypatch):
    """
    Verify that images exceeding MASSIVE_IMAGE_THRESHOLD are rejected upfront.
    """
    # Create temp source dir and output dir
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    
    # Create one normal image and one massive image file
    normal_img = src_dir / "normal.jpg"
    normal_img.write_text("dummy")
    massive_img = src_dir / "massive.jpg"
    massive_img.write_text("dummy")
    
    # Mock HeuristicInterpolator and converters
    with patch("app.batch_api.orchestrator.HeuristicInterpolator"), \
         patch("app.batch_api.orchestrator.MagickConverter") as mock_conv_cls:
         
        mock_conv = mock_conv_cls.return_value
        mock_conv.convert_batch.return_value = {
            "success_count": 1,
            "failure_count": 0,
            "duration_ms": 10.0,
            "telemetry": {},
            "errors": []
        }
        mock_conv.is_broken = False
        
        orch = BatchOrchestrator()
        
        # Mock repo
        mock_repo = MagicMock()
        orch.repo = mock_repo
        
        # Mock _probe_all_dimensions to return dimensions:
        # normal.jpg -> 1000x1000 = 1MP
        # massive.jpg -> 10000x10000 = 100MP (> 50MP threshold)
        def mock_probe_all_dimensions(paths):
            res = {}
            for p in paths:
                if "normal" in p:
                    res[p] = (1000, 1000)
                elif "massive" in p:
                    res[p] = (10000, 10000)
            return res
            
        monkeypatch.setattr(orch, "_probe_all_dimensions", mock_probe_all_dimensions)
        
        # Mock _preflight_resources to pass
        monkeypatch.setattr(orch, "_preflight_resources", lambda x: None)
        
        # Execute batch request
        request = BatchRequest(
            source_dir=str(src_dir),
            target_dir=str(dst_dir),
            target_format=["webp"],
            tool=["magick"]
        )
        
        # Patch db connection context
        with patch("app.batch_api.orchestrator.get_connection") as mock_get_conn:
            mock_get_conn.return_value.__enter__.return_value = MagicMock()
            orch.execute_batch(run_id=123, request=request)
            
        # The converter should have been called with ONLY normal.jpg, NOT massive.jpg!
        mock_conv.convert_batch.assert_called_once()
        called_args = mock_conv.convert_batch.call_args[0]
        # First arg is input_paths list
        input_paths_sent = called_args[0]
        assert len(input_paths_sent) == 1
        assert "normal.jpg" in input_paths_sent[0]
        assert "massive.jpg" not in input_paths_sent[0]
        
        # Check that we logged a failure for massive.jpg
        # Since it is 1 image * 1 cell = 1 failure upfront
        mock_repo.save_summary.assert_called_once()
        kwargs = mock_repo.save_summary.call_args[1]
        # Total failures: 0 from converter + 1 upfront = 1
        assert kwargs["failure_count"] == 1
        assert kwargs["success_count"] == 1


def test_probe_failure_does_not_crash_batch(tmp_path, monkeypatch):
    """_probe_all_dimensions must not crash when one file is unreadable.
    Unreadable files (probe returns (0,0)) must be counted as failures, not crash the batch."""
    from unittest.mock import patch, MagicMock

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    good = src_dir / "good.jpg"
    good.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
    bad = src_dir / "bad.jpg"
    bad.write_bytes(b"notanimage")

    with patch("app.batch_api.orchestrator.HeuristicInterpolator"), \
         patch("app.batch_api.orchestrator.MagickConverter") as mock_cls, \
         patch("app.batch_api.orchestrator.FFmpegConverter"), \
         patch("app.batch_api.orchestrator.VipsConverter"), \
         patch("app.batch_api.orchestrator.SharpConverter"):
        mock_conv = mock_cls.return_value
        mock_conv.is_broken = False
        mock_conv.convert_batch.return_value = {
            "success_count": 0, "failure_count": 1,
            "duration_ms": 5.0, "telemetry": {}, "errors": []
        }
        orch = BatchOrchestrator()
        orch.repo = MagicMock()
        monkeypatch.setattr(orch, "_preflight_resources", lambda x: None)

        def probe_raises(paths):
            result = {}
            for p in paths:
                if "bad" in p:
                    result[p] = (0, 0)
                else:
                    result[p] = (100, 100)
            return result

        monkeypatch.setattr(orch, "_probe_all_dimensions", probe_raises)

        req = BatchRequest(
            source_dir=str(src_dir),
            target_dir=str(dst_dir),
            target_format=["avif"],
            tool=["magick"],
        )
        with patch("app.batch_api.orchestrator.get_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value = MagicMock()
            orch.execute_batch(run_id=1, request=req)

        kwargs = orch.repo.save_summary.call_args[1]
        assert kwargs["failure_count"] >= 1, "bad.jpg must be counted as failure"


def test_unreadable_image_not_sent_to_converter(tmp_path, monkeypatch):
    """Images with (0,0) probe result must be excluded from convert_batch call."""
    from unittest.mock import patch, MagicMock

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    (src_dir / "ok.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)
    (src_dir / "bad.jpg").write_bytes(b"garbage")

    with patch("app.batch_api.orchestrator.HeuristicInterpolator"), \
         patch("app.batch_api.orchestrator.MagickConverter") as mock_cls, \
         patch("app.batch_api.orchestrator.FFmpegConverter"), \
         patch("app.batch_api.orchestrator.VipsConverter"), \
         patch("app.batch_api.orchestrator.SharpConverter"):
        mock_conv = mock_cls.return_value
        mock_conv.is_broken = False
        mock_conv.convert_batch.return_value = {
            "success_count": 1, "failure_count": 0,
            "duration_ms": 5.0, "telemetry": {}, "errors": []
        }
        orch = BatchOrchestrator()
        orch.repo = MagicMock()
        monkeypatch.setattr(orch, "_preflight_resources", lambda x: None)
        monkeypatch.setattr(orch, "_probe_all_dimensions",
                            lambda paths: {p: (0, 0) if "bad" in p else (100, 100) for p in paths})

        req = BatchRequest(
            source_dir=str(src_dir),
            target_dir=str(dst_dir),
            target_format=["avif"],
            tool=["magick"],
        )
        with patch("app.batch_api.orchestrator.get_connection") as mock_conn:
            mock_conn.return_value.__enter__.return_value = MagicMock()
            orch.execute_batch(run_id=2, request=req)

        called_paths = mock_conv.convert_batch.call_args[0][0]
        assert all("bad" not in p for p in called_paths), "bad.jpg must not reach converter"
