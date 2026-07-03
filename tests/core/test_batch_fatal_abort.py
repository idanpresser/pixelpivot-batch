"""7.1 — mid-batch circuit-breaker trip on consecutive fatal errors.

A dead tool (missing binary, broken libaom) produces a *fatal* error on every
file. The default ThreadPoolExecutor batch path must abort the remaining files
after a threshold of consecutive fatal errors instead of attempting all N, while
still reporting every file as an error so the orchestrator quarantines it to DLQ.

This must NOT change behaviour for ordinary per-file (non-fatal) failures — those
are covered by tests/test_circuit_breaker_isolation.py and must keep flowing.
"""
from typing import List, Union, Optional

import app.core.converters.base as base_mod
from app.core.converters.base import BaseConverter, ConvertResult


class _DeadToolConverter(BaseConverter):
    """Concrete converter whose every convert() call is a fatal failure."""

    def __init__(self):
        super().__init__()
        self.convert_calls = 0

    def get_name(self) -> str:
        return "deadtool"

    def supported_formats(self) -> List[str]:
        return ["webp"]

    def convert(
        self,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
        is_intermediate: bool = False,
        run_id: Optional[int] = None,
    ) -> ConvertResult:
        self.convert_calls += 1
        return ConvertResult(
            success=False,
            error="deadtool: No such file or directory",
            fatal_error=True,
        )


def test_batch_aborts_after_threshold_consecutive_fatal_errors(tmp_path, monkeypatch):
    # Force single-worker so the abort point is deterministic.
    monkeypatch.setattr(base_mod, "CONCURRENT_ENCODES_MAX_WORKERS", 1)

    conv = _DeadToolConverter()
    threshold = base_mod.BATCH_FATAL_ABORT_THRESHOLD

    n_files = threshold + 7
    files = []
    for i in range(n_files):
        p = tmp_path / f"img_{i}.png"
        p.write_bytes(b"dummy")
        files.append(str(p))

    result = conv.convert_batch(
        files,
        str(tmp_path / "out"),
        "webp",
        [80.0] * n_files,
    )

    # Aborted after the threshold — did NOT attempt every file.
    assert conv.convert_calls == threshold, (
        f"expected exactly {threshold} attempts before abort, got {conv.convert_calls}"
    )
    # Every file still reported as a failure so the orchestrator routes it to DLQ.
    assert result["failure_count"] == n_files
    assert result["success_count"] == 0
    assert len(result["errors"]) == n_files
    reported = {e["path"] for e in result["errors"]}
    for f in files:
        assert f in reported, f"{f} missing from batch errors — would escape DLQ"
