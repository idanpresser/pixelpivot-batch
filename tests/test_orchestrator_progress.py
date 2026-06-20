# tests/test_orchestrator_progress.py
from unittest.mock import patch
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest, Tool

class _FakeConverter:
    is_broken = False
    def _reset_failures(self): pass
    def convert_batch(self, paths, target_dir, fmt, qualities, **kw):
        return {"success_count": len(paths), "failure_count": 0, "errors": [], "telemetry": None}

def test_progress_published_during_run(tmp_path):
    src = tmp_path / "src"; src.mkdir()
    (src / "a.jpg").write_bytes(b"x")
    req = BatchRequest(source_dir=str(src), target_dir=str(tmp_path / "dst"),
                       target_format=["webp", "avif"], tool=[Tool.magick], category=["general"])
    orch = BatchOrchestrator()
    orch.converters = {"magick": _FakeConverter()}
    seen = {}
    real_cb = _FakeConverter.convert_batch
    def spy(self, *a, **k):
        # capture progress snapshot mid-run
        seen.update(dict(orch.progress.get(1, {})))
        return real_cb(self, *a, **k)
    with patch.object(_FakeConverter, "convert_batch", spy), \
         patch.object(orch.repo, "update_status"), \
         patch.object(orch.repo, "save_summary"), \
         patch.object(orch, "_preflight_resources"), \
         patch.object(orch, "_probe_all_dimensions",
                      return_value={str(src / "a.jpg"): (10, 10)}):
        orch.execute_batch(1, req)
    assert seen.get("cells_total") == 2
    assert "current_cell" in seen
