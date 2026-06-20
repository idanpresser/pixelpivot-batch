import pytest
from unittest.mock import MagicMock, patch
from app.core.converters.magick_converter import MagickConverter

@pytest.fixture
def converter():
    return MagickConverter(magick_path="magick")

def test_magick_batch_grouping(converter):
    input_paths = ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]
    # Grouping by quality: a and c get 80, b and d get 90
    qualities = [80, 90, 80, 90]
    output_dir = "out"

    with patch("app.core.converters.magick_converter.get_resolution_bucket_from_path", return_value="medium"), \
         patch("app.core.converters.magick_converter.subprocess.Popen") as mock_popen:
        # `with subprocess.Popen(...) as proc:` binds proc to __enter__'s return.
        mock_proc = mock_popen.return_value.__enter__.return_value
        mock_proc.pid = 123
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = ("", "")

        # Mock TelemetryMonitor. monitor.stop() must return a real dict — the
        # aggregator does max(float, sample[key]) and MagicMock won't compare.
        with patch("app.core.converters.magick_converter.TelemetryMonitor") as mock_tm:
            mock_tm.return_value.stop.return_value = {
                "cpu_avg": 0.0, "cpu_peak": 0.0, "ram_peak": 0.0,
            }
            result = converter.convert_batch(input_paths, output_dir, "webp", qualities)

            assert result["success_count"] == 4
            assert result["failure_count"] == 0
            
            # Should have called Popen twice (once for each quality group)
            assert mock_popen.call_count == 2
            
            # Check one of the calls
            args, _ = mock_popen.call_args_list[0]
            cmd = args[0]
            assert "mogrify" in cmd or "magick" in cmd
            assert "-path" in cmd
            assert "out" in cmd
            assert "-format" in cmd
            assert "webp" in cmd
            assert "-quality" in cmd
            # One call should have 80, another 90
            qs = [args[0][cmd.index("-quality") + 1] for args, _ in mock_popen.call_args_list]
            assert set(qs) == {"80", "90"}

def test_magick_batch_partial_failure(converter):
    input_paths = ["a.jpg", "b.jpg"]
    qualities = [80, 80]
    output_dir = "out"
    
    with patch("app.core.converters.magick_converter.get_resolution_bucket_from_path", return_value="medium"), \
         patch("app.core.converters.magick_converter.subprocess.Popen") as mock_popen:
        mock_proc = mock_popen.return_value.__enter__.return_value
        mock_proc.pid = 123
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = ("", "mogrify error: some files failed")
        
        with patch("app.core.converters.magick_converter.TelemetryMonitor"):
            result = converter.convert_batch(input_paths, output_dir, "webp", qualities)
            
            # If mogrify fails, we might not know exactly which files failed 
            # unless we parse the output very carefully. 
            # For now, let's assume if it fails, all files in that group fail.
            assert result["success_count"] == 0
            assert result["failure_count"] == 2
            assert len(result["errors"]) > 0
