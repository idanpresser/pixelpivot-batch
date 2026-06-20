# tests/test_orchestrator_cancel.py
from unittest.mock import patch
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest, Tool

class _FakeConverter:
    is_broken = False
    def _reset_failures(self): pass
    def convert_batch(self, paths, target_dir, fmt, qualities, **kw):
        return {"success_count": len(paths), "failure_count": 0, "errors": [], "telemetry": None}

def _req(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "a.jpg").write_bytes(b"x")
    dst = tmp_path / "dst"
    return BatchRequest(source_dir=str(src), target_dir=str(dst),
                        target_format=["webp", "avif"], tool=[Tool.magick], category=["general"])

def test_cancel_before_run_marks_cancelled(tmp_path):
    orch = BatchOrchestrator()
    orch.converters = {"magick": _FakeConverter()}
    req = _req(tmp_path)
    # Pre-register a cancelled control for run 7
    from app.batch_api.run_control import RunControl
    ctrl = RunControl(); ctrl.cancel()
    orch.run_controls[7] = ctrl
    captured = {}
    def fake_update(conn, run_id, status, total_images=None):
        captured["status"] = status
    with patch.object(orch.repo, "update_status", side_effect=fake_update), \
         patch.object(orch, "_probe_all_dimensions", return_value={str(tmp_path / "src" / "a.jpg"): (10, 10)}), \
         patch.object(orch, "_preflight_resources"):
        orch.execute_batch(7, req)
    assert captured["status"] == "cancelled"
    assert 7 not in orch.run_controls   # cleaned up
