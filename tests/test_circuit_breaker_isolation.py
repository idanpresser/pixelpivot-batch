import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from app.core.converters.magick_converter import MagickConverter
from app.batch_api.orchestrator import BatchOrchestrator
from app.batch_api.models import BatchRequest

class _FakeMonitor:
    def __init__(self, *a, **k): pass
    def start(self): pass
    def stop(self): return {}

def test_circuit_breaker_does_not_auto_fail_untried_files_in_batch(tmp_path, monkeypatch):
    """
    Test for issue 49x: circuit breaker tool-level, not file-level.
    When a batch with multiple files runs:
    - 3 files fail (which would normally trip the breaker).
    - The 4th file is still attempted and, if valid, succeeds and resets the breaker.
    """
    conv = MagickConverter(magick_path="magick")
    
    # Mock subprocess.Popen and TelemetryMonitor
    monkeypatch.setattr("app.core.converters.magick_converter.TelemetryMonitor", _FakeMonitor)
    
    # We will simulate convert outcomes
    outcomes = [
        {"success": False, "error": "fake error 1"},
        {"success": False, "error": "fake error 2"},
        {"success": False, "error": "fake error 3"},
        {"success": True, "error": None, "telemetry": {}},
    ]
    outcome_iter = iter(outcomes)
    
    def mock_convert(input_path, output_path, target_format, quality, **kwargs):
        return next(outcome_iter)
        
    monkeypatch.setattr(conv, "convert", mock_convert)
    
    # Create 4 dummy files
    files = []
    for i in range(4):
        p = tmp_path / f"img_{i}.png"
        p.write_bytes(b"dummy")
        files.append(str(p))
        
    dims = {f: (100, 100) for f in files}
    
    # We run the batch. MagickConverter.convert_batch should fallback to converting one-by-one.
    # We mock _run_image2_path/mogrify path to trigger fallback.
    # Actually, we can just patch subprocess.Popen to return code 1 for mogrify so fallback is used.
    class _FakeProc:
        def __init__(self, *a, **k):
            self.pid = 999
            self.returncode = 1
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def communicate(self, timeout=None):
            return ("", "mogrify chunk failed")
            
    monkeypatch.setattr("app.core.converters.magick_converter.subprocess.Popen", _FakeProc)
    
    result = conv.convert_batch(
        files,
        str(tmp_path / "out"),
        "webp",
        [80.0] * 4,
        dimensions=dims
    )
    
    # Verify outcomes
    assert result["success_count"] == 1
    assert result["failure_count"] == 3
    # The breaker should NOT be broken at the end because we didn't reach the threshold
    assert conv.is_broken is False


def test_execute_batch_resets_breaker_so_prior_batch_failures_do_not_bleed(tmp_path):
    """Issue 49x cross-batch bleed: the orchestrator is a long-lived singleton, so
    a converter instance left is_broken=True by one batch must NOT carry that state
    into the next batch and quarantine healthy files during the 30s cooldown.
    execute_batch must reset each converter's breaker at the start of every run.
    """
    with patch("app.batch_api.orchestrator.HeuristicInterpolator"):
        with patch("app.batch_api.orchestrator.MagickConverter"):
            with patch("app.batch_api.orchestrator.BatchRepository") as mock_repo_class:
                orch = BatchOrchestrator()
                orch.repo = mock_repo_class.return_value

    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "good.jpg").write_bytes(b"x")

    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(tmp_path / "dst"),
        target_format=["webp"],
        tool=["magick"],
    )

    conv = orch.converters["magick"]
    # Simulate a prior batch having tripped the breaker on this reused instance.
    conv.is_broken = True
    conv.consecutive_failures = 5
    # Model the real BaseConverter._reset_failures so the mock reflects a true reset.
    def _reset():
        conv.is_broken = False
        conv.consecutive_failures = 0
    conv._reset_failures.side_effect = _reset
    conv.convert_batch.return_value = {
        "success_count": 1, "failure_count": 0, "duration_ms": 1.0, "errors": [],
    }

    with patch("app.batch_api.orchestrator.get_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value = MagicMock()
        with patch("app.core.utils.probe_image_dimensions", return_value=(100, 100)):
            orch.execute_batch(run_id=3, request=request)

    # Breaker must have been reset, so the healthy file was actually converted,
    # not quarantined as collateral from the previous batch.
    assert conv.is_broken is False, "Breaker not reset at batch start; cross-batch bleed"
    assert conv.convert_batch.called, "convert_batch never invoked — file wrongly quarantined"


@pytest.fixture
def _orch():
    with patch("app.batch_api.orchestrator.HeuristicInterpolator"):
        with patch("app.batch_api.orchestrator.MagickConverter"):
            with patch("app.batch_api.orchestrator.BatchRepository") as mock_repo_class:
                orch = BatchOrchestrator()
                orch.repo = mock_repo_class.return_value
                return orch


def test_orchestrator_broken_converter_reports_per_file_errors(_orch, tmp_path):
    """When converter.is_broken, orchestrator must add one error entry per file (not one N/A).

    Issue 49x: previously a single {"path": "N/A"} entry silently swallowed all
    individual file paths, making quarantined files invisible to callers.
    """
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    files = []
    for i in range(3):
        p = source_dir / f"img_{i}.jpg"
        p.write_bytes(b"x")
        files.append(str(p))

    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(tmp_path / "dst"),
        target_format=["webp"],
        tool=["magick"],
    )

    mock_converter = _orch.converters["magick"]
    mock_converter.is_broken = True

    with patch("app.batch_api.orchestrator.get_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value = MagicMock()
        # Probe must succeed so files survive the upfront unreadable filter and
        # actually reach the is_broken quarantine branch.
        with patch("app.core.utils.probe_image_dimensions", return_value=(100, 100)):
            _orch.execute_batch(run_id=1, request=request)

    assert _orch.repo.save_errors.called, "save_errors not called"
    # save_errors(conn, run_id, errors) — errors is positional arg index 2
    errors = _orch.repo.save_errors.call_args[0][2]
    # Should have one error per file, not one generic N/A error
    assert len(errors) == 3, f"Expected 3 per-file errors, got {len(errors)}: {errors}"
    error_paths = {e["path"] for e in errors}
    assert "N/A" not in error_paths, "Generic N/A error found; per-file errors required"
    for f in files:
        assert f in error_paths, f"{f} not in quarantine errors"


def test_orchestrator_broken_converter_errors_have_quarantine_marker(_orch, tmp_path):
    """Each quarantine error must carry a 'quarantined': True flag."""
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (source_dir / "img.jpg").write_bytes(b"x")

    request = BatchRequest(
        source_dir=str(source_dir),
        target_dir=str(tmp_path / "dst"),
        target_format=["webp"],
        tool=["magick"],
    )

    _orch.converters["magick"].is_broken = True

    with patch("app.batch_api.orchestrator.get_connection") as mock_conn:
        mock_conn.return_value.__enter__.return_value = MagicMock()
        with patch("app.core.utils.probe_image_dimensions", return_value=(100, 100)):
            _orch.execute_batch(run_id=2, request=request)

    assert _orch.repo.save_errors.called, "save_errors not called"
    errors = _orch.repo.save_errors.call_args[0][2]
    assert errors, "No errors recorded"
    for e in errors:
        assert e.get("quarantined") is True, f"Missing quarantined flag: {e}"


def test_recover_chunk_per_file_restores_run_state_symmetrically(tmp_path, monkeypatch):
    """qk1.4: _recover_chunk_per_file must save and restore the SAME breaker
    state key. It sets active run_id AFTER reading the save snapshot, so with
    per-run isolation the save read a different (entry) run's state than the
    restore wrote — corrupting the batch run's breaker fields.

    Invariant: with no concurrent run, the run's breaker fields are identical
    before and after recovery.
    """
    conv = MagickConverter(magick_path="magick")
    monkeypatch.setattr(
        "app.core.converters.magick_converter.TelemetryMonitor", _FakeMonitor
    )

    run_id = 5
    # Seed the batch run's mid-flight breaker state: one prior failure recorded.
    conv._set_active_run_id(run_id)
    conv.consecutive_failures = 1
    before_failures = conv.consecutive_failures
    before_broken = conv.is_broken
    before_broken_since = conv.broken_since

    # Emulate entry from the convert_batch thread, which never set active run_id.
    conv._set_active_run_id(None)

    def failing_convert(input_path, output_path, target_format, quality, **kwargs):
        return {"success": False, "error": "boom"}

    monkeypatch.setattr(conv, "convert", failing_convert)

    files = []
    for i in range(3):
        p = tmp_path / f"f{i}.png"
        p.write_bytes(b"x")
        files.append(str(p))

    conv._recover_chunk_per_file(files, str(tmp_path / "out"), "webp", 80.0, "", run_id)

    # The batch run's breaker fields must be exactly what they were before.
    conv._set_active_run_id(run_id)
    assert conv.consecutive_failures == before_failures
    assert conv.is_broken == before_broken
    assert conv.broken_since == before_broken_since


def test_circuit_breaker_concurrency():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from app.core.converters.base import BaseConverter
    from typing import List

    class ConcurrencyTestConverter(BaseConverter):
        def get_name(self) -> str:
            return "concurrency_test"
        def supported_formats(self) -> List[str]:
            return ["webp"]
        def convert(self, *args, **kwargs):
            pass

    conv = ConcurrencyTestConverter()
    conv.failure_threshold = 1000

    M = 100
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = [executor.submit(conv._mark_failure) for _ in range(M)]
        for f in futures:
            f.result()

    assert conv.consecutive_failures == M

