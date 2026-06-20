"""Tests for _win32_safe_path — Windows long-path and UNC prefixing."""
import sys
import pytest
from app.core.converters.base import _win32_safe_path

PREFIX_LOCAL = "\\\\?\\"       # \\?\
PREFIX_UNC   = "\\\\?\\UNC\\"  # \\?\UNC\


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_local_absolute_path_gets_prefix():
    result = _win32_safe_path("C:\\some\\path\\file.jpg")
    assert result.startswith(PREFIX_LOCAL)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_unc_path_gets_unc_prefix():
    result = _win32_safe_path("\\\\server\\share\\pics\\file.jpg")
    assert result.startswith(PREFIX_UNC)
    assert "server" in result
    assert "share" in result


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_already_prefixed_path_unchanged():
    already = "\\\\?\\C:\\some\\path\\file.jpg"
    assert _win32_safe_path(already) == already


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_relative_path_unchanged():
    assert _win32_safe_path("relative/path.jpg") == "relative/path.jpg"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only")
def test_mapped_drive_z_gets_prefix():
    result = _win32_safe_path("Z:\\pics\\real\\image.jpg")
    assert result.startswith(PREFIX_LOCAL)
    assert "pics" in result
