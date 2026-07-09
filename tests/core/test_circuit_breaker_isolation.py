import threading
import time
import pytest
from app.core.converters.base import BaseConverter


class _RunStateConverter(BaseConverter):
    """Minimal converter to exercise per-run breaker state in isolation."""

    def get_name(self) -> str:
        return "run_state"

    def supported_formats(self) -> list[str]:
        return ["webp"]

    def convert(self, *args, **kwargs):
        return {"success": True, "telemetry": {}}


def test_isolated_run_ignores_global_none_breaker():
    """qk1.3: a breaker tripped in the global (run_id=None) context must NOT
    leak into an isolated run. Run B never failed, so it must read healthy."""
    conv = _RunStateConverter()
    conv.failure_threshold = 3

    # Global/default context (run_id=None) trips the breaker.
    conv._set_active_run_id(None)
    for _ in range(3):
        conv._mark_failure()
    assert conv.is_broken is True

    # Run B starts; it has its own run_id and never failed.
    conv._set_active_run_id(2)
    assert conv.is_broken is False, "Global None breaker leaked into isolated run B"
    assert conv.consecutive_failures == 0, "Global None failure count leaked into run B"


def test_reset_in_run_does_not_wipe_other_run_state():
    """qk1.3: run A recovering (reset) must not clear the breaker that run B
    reads. Runs must be mutually isolated via their own state keys."""
    conv = _RunStateConverter()
    conv.failure_threshold = 3

    # Run B trips its own breaker.
    conv._set_active_run_id(2)
    for _ in range(3):
        conv._mark_failure()
    assert conv.is_broken is True

    # Run A independently fails then recovers.
    conv._set_active_run_id(1)
    for _ in range(3):
        conv._mark_failure()
    conv._reset_failures()

    # Run B must still be broken — run A's reset must not have cleared it.
    conv._set_active_run_id(2)
    assert conv.is_broken is True, "Run A reset cleared run B breaker (cross-run interference)"
    assert conv.consecutive_failures == 3

class DummyBreakerConverter(BaseConverter):
    def __init__(self):
        super().__init__()
        self.conversion_started = threading.Event()
        self.batch_b_finished = threading.Event()
        self.bypass_checked_on_a = threading.Event()
        self.bypass_value_on_a = None

    def get_name(self) -> str:
        return "dummy_breaker"

    def supported_formats(self) -> list[str]:
        return ["webp"]

    def convert(self, input_path, output_path, target_format, quality, run_id=None):
        self._set_active_run_id(run_id)
        if run_id == 1:
            # Batch A: signal we started, then wait for Batch B to finish
            self.conversion_started.set()
            # Wait for Batch B to set its finished event (with timeout to avoid hang)
            self.batch_b_finished.wait(timeout=2.0)
            # Check bypass value for Batch A
            self.bypass_value_on_a = self._bypass_breaker
            self.bypass_checked_on_a.set()
        return {"success": True, "telemetry": {}}

def test_concurrent_batches_bypass_breaker_isolation(tmp_path):
    """
    Test that concurrent batches run on the same converter do not interfere
    with each other's _bypass_breaker state.
    """
    conv = DummyBreakerConverter()
    
    # We will run Batch A in a thread
    def run_batch_a():
        conv.convert_batch(
            input_paths=[str(tmp_path / "dummy_a.webp")],
            output_dir=str(tmp_path),
            target_format="webp",
            qualities=[80],
            run_id=1
        )

    t_a = threading.Thread(target=run_batch_a)
    t_a.start()

    # Wait for Batch A's convert to actually start and enable bypass
    assert conv.conversion_started.wait(timeout=2.0)

    # Now, run a quick Batch B on the same converter
    # It has a different run_id=2, so it will set bypass_breaker = True for run 2,
    # then finish and in finally block set bypass_breaker = False for run 2.
    conv.convert_batch(
        input_paths=[str(tmp_path / "dummy_b.webp")],
        output_dir=str(tmp_path),
        target_format="webp",
        qualities=[80],
        run_id=2
    )

    # Signal Batch A that Batch B has finished
    conv.batch_b_finished.set()

    # Wait for Batch A's convert to finish checking bypass
    assert conv.bypass_checked_on_a.wait(timeout=2.0)
    
    # Clean up thread
    t_a.join(timeout=2.0)

    # Assert that Batch A's bypass was STILL True when checked,
    # despite Batch B having finished and set its bypass to False.
    assert conv.bypass_value_on_a is True
