"""Guard tests for bd-bze: Windows Service deployment artifacts.

The FastAPI process must be runnable as an auto-restarting Windows Service that
survives reboot/crash. These guard the existence and key contract of the NSSM
install script + doc so they can't silently regress or be deleted.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_install_script_exists_and_configures_autorestart():
    script = ROOT / "scripts" / "install_windows_service.ps1"
    assert script.exists()
    text = script.read_text(encoding="utf-8")
    # Reboot persistence + crash auto-restart are the bze acceptance core.
    assert "SERVICE_AUTO_START" in text
    assert "AppExit Default Restart" in text
    # Admin guard so install doesn't half-apply under a non-elevated shell.
    assert "Administrator" in text


def test_windows_service_doc_exists_and_recommends_nssm():
    doc = ROOT / "docs" / "WINDOWS_SERVICE.md"
    assert doc.exists()
    text = doc.read_text(encoding="utf-8")
    assert "NSSM" in text
    assert "install_windows_service.ps1" in text
