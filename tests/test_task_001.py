import pytest
from unittest.mock import MagicMock, patch
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest, Tool

def test_tool_enum_suffix_no_tool_prefix(tmp_path):
    """
    Regression test for Task 001: The orchestrator should not leak the Tool enum member
    name into the output file suffix or logs.
    """
    request = BatchRequest(
        source_dir=str(tmp_path),
        target_dir=str(tmp_path),
        category=["highRes"],
        tool=[Tool.magick, Tool.ffmpeg],
        target_format=["webp"]
    )

    # Mock os.listdir / is_file to pretend there is one image
    with patch("pathlib.Path.is_file", return_value=True), \
         patch("pathlib.Path.iterdir", return_value=[tmp_path / "test.jpg"]), \
         patch("os.path.getsize", return_value=1024), \
         patch("app.core.utils.probe_image_dimensions", return_value=(800, 600)):
        
        orchestrator = BatchOrchestrator()
        orchestrator.repo = MagicMock()

        # A real converter writes outputs DURING convert_batch; mirror that so the
        # savings math (which only credits files produced this run) sees them.
        def _write_outputs(input_paths, output_dir, target_format, qualities,
                           run_id=None, suffix="", dimensions=None):
            from pathlib import Path as _P
            for p in input_paths:
                (_P(output_dir) / f"{_P(p).stem}{suffix}.{target_format}").write_bytes(b"a" * 512)
            return {"success_count": len(input_paths), "failure_count": 0, "errors": [], "telemetry": {}}

        mock_magick = MagicMock()
        mock_magick.is_broken = False
        mock_magick.convert_batch.side_effect = _write_outputs

        mock_ffmpeg = MagicMock()
        mock_ffmpeg.is_broken = False
        mock_ffmpeg.convert_batch.side_effect = _write_outputs

        orchestrator.converters = {
            "magick": mock_magick,
            "ffmpeg": mock_ffmpeg
        }

        orchestrator.execute_batch(1, request)

        # Assert that the magick converter was called with suffix='_magick' (not '_Tool.magick')
        mock_magick.convert_batch.assert_called_once()
        kwargs = mock_magick.convert_batch.call_args.kwargs
        assert kwargs.get("suffix") == "_magick", f"Expected _magick, got {kwargs.get('suffix')}"

        # Assert that ffmpeg converter was called with suffix='_ffmpeg'
        mock_ffmpeg.convert_batch.assert_called_once()
        kwargs2 = mock_ffmpeg.convert_batch.call_args.kwargs
        assert kwargs2.get("suffix") == "_ffmpeg", f"Expected _ffmpeg, got {kwargs2.get('suffix')}"

        # Verify savings_pct calculation (Criterion 4)
        # 1 image * 2 tools * 1 format = 2 output lookups
        # Input: 1024 bytes (mocked in outer patch) * 2 lookups = 2048
        # Output: 512 bytes * 2 lookups = 1024
        # Savings: (1 - 1024/2048) * 100 = 50.0%
        orchestrator.repo.save_summary.assert_called_once()
        summary_kwargs = orchestrator.repo.save_summary.call_args.kwargs
        assert summary_kwargs["savings_pct"] == 50.0, f"Expected 50.0% savings, got {summary_kwargs['savings_pct']}"
