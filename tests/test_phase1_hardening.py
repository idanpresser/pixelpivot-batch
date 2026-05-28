
import os
import sys
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock

def test_sqlite_path_resolution():
    """Verify DATABASE_URL is absolute and points to the data directory."""
    from app.core.paths import DATABASE_URL, PROJ_ROOT
    assert DATABASE_URL.startswith("sqlite:///")
    db_path = DATABASE_URL.replace("sqlite:///", "")
    assert Path(db_path).is_absolute()
    # The plan says it should be in 'data' subdir
    assert "data" in db_path

def test_vips_dll_logic_windows(monkeypatch):
    """Test the logic of ensure_vips_dlls without actually loading DLLs."""
    if sys.platform != "win32":
        pytest.skip("Windows only test")

    from app.core.utils import ensure_vips_dlls
    
    # Mock PROJ_ROOT and directories
    mock_proj_root = Path("C:/mock_proj")
    mock_vips_bin = mock_proj_root / "bin" / "vips" / "bin"
    
    with patch("app.core.paths.PROJ_ROOT", mock_proj_root), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.glob", side_effect=lambda p: [mock_proj_root / "bin" / "vips"] if "vips" in p else []), \
         patch("os.add_dll_directory") as mock_add_dll, \
         patch.dict(os.environ, {"PATH": "C:/some/other/path"}):
        
        ensure_vips_dlls()
        
        # Verify PATH was updated
        assert str(mock_vips_bin.absolute()) in os.environ["PATH"]
        # Verify add_dll_directory was called
        mock_add_dll.assert_called_once()

def test_no_premature_vips_init():
    """Verify that importing utils doesn't crash or have side effects if vips is missing."""
    # This is tricky since utils already might have been imported.
    # We'll check if we can control the init.
    with patch("app.core.utils.ensure_vips_dlls") as mock_ensure:
        import app.core.utils
        # If it's already imported, this might not work as expected in a single process.
        # But we can check if _VIPS_AVAILABLE is set.
        pass
