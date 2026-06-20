import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.cli import main, check_paths


def _capture_exit(monkeypatch):
    """Patch sys.exit to capture the code instead of terminating."""
    holder = {"code": None}
    monkeypatch.setattr(sys, "exit", lambda code=0: holder.__setitem__("code", code))
    return holder


def test_cli_validation_flow(tmp_path, monkeypatch):
    """
    Test that the CLI performs validation correctly and exits with code 0 on success.
    """
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    
    # Mock arg parsing
    test_args = ["pixelpivot-cli", "-s", str(src_dir), "-t", str(dst_dir), "--dry-run"]
    monkeypatch.setattr(sys, "argv", test_args)
    
    # Mock binary checking, pyvips loading, and sharp daemon
    with patch("app.cli.check_binary", return_value=True) as mock_check_binary, \
         patch("app.cli.check_pyvips", return_value=True) as mock_check_pyvips, \
         patch("app.cli.check_sharp_daemon", return_value=True) as mock_check_sharp:
         
        # Mock sys.exit to capture the exit code
        exit_code = None
        def mock_exit(code):
            nonlocal exit_code
            exit_code = code
            
        monkeypatch.setattr(sys, "exit", mock_exit)
        
        main()
        
        assert exit_code == 0
        assert mock_check_binary.call_count == 2
        mock_check_pyvips.assert_called_once()
        mock_check_sharp.assert_called_once()


def test_cli_exits_1_when_source_missing(tmp_path, monkeypatch):
    """A non-existent source directory fails validation -> exit code 1."""
    missing_src = tmp_path / "does_not_exist"
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    monkeypatch.setattr(
        sys, "argv",
        ["pixelpivot-cli", "-s", str(missing_src), "-t", str(dst_dir), "--dry-run"],
    )
    holder = _capture_exit(monkeypatch)
    with patch("app.cli.check_binary", return_value=True), \
         patch("app.cli.check_pyvips", return_value=True), \
         patch("app.cli.check_sharp_daemon", return_value=True):
        main()
    assert holder["code"] == 1


def test_cli_exits_1_when_binary_missing(tmp_path, monkeypatch):
    """A missing native binary fails validation -> exit code 1."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()
    monkeypatch.setattr(
        sys, "argv",
        ["pixelpivot-cli", "-s", str(src_dir), "-t", str(dst_dir), "--dry-run"],
    )
    holder = _capture_exit(monkeypatch)
    with patch("app.cli.check_binary", return_value=False), \
         patch("app.cli.check_pyvips", return_value=True), \
         patch("app.cli.check_sharp_daemon", return_value=True):
        main()
    assert holder["code"] == 1


def test_check_paths_false_when_source_absent(tmp_path):
    """check_paths reports failure when the source path does not exist."""
    assert check_paths(str(tmp_path / "nope"), str(tmp_path / "out")) is False


def test_check_paths_true_when_dirs_valid(tmp_path):
    """check_paths succeeds for a readable source and writable target."""
    src = tmp_path / "in"
    src.mkdir()
    assert check_paths(str(src), str(tmp_path / "out")) is True
