from unittest.mock import MagicMock, patch
from app.tui import launcher

def test_run_tui_starts_api_then_runs_app():
    fake_sup = MagicMock()
    fake_sup.wait_ready.return_value = True
    fake_app = MagicMock()
    with patch.object(launcher, "ProcessSupervisor", return_value=fake_sup), \
         patch.object(launcher, "build_application", return_value=fake_app), \
         patch.object(launcher, "TuiApiClient", return_value=MagicMock()):
        launcher.run_tui()
    fake_sup.start.assert_called()          # API child spawned
    fake_sup.wait_ready.assert_called_once()
    fake_app.run.assert_called_once()
    fake_sup.shutdown.assert_called_once()  # cleaned up on exit
