"""Per-run cooperative pause/cancel control for batch execution.

A RunControl is checked by the orchestrator at matrix-cell boundaries. Pause
blocks the executing thread on an Event; cancel sets a flag and releases any
paused waiter so the loop can observe cancellation and exit.
"""
import threading
from typing import Dict


class RunControl:
    """Cooperative pause/resume/cancel signal for a single batch run."""

    def __init__(self) -> None:
        self._resume = threading.Event()
        self._resume.set()          # running by default
        self.paused = False
        self.cancelled = False

    def pause(self) -> None:
        self.paused = True
        self._resume.clear()

    def resume(self) -> None:
        self.paused = False
        self._resume.set()

    def cancel(self) -> None:
        self.cancelled = True
        self._resume.set()          # release a paused waiter so it can exit

    def wait_if_paused(self, timeout: float | None = None) -> None:
        self._resume.wait(timeout)


# run_id -> RunControl
RunControlRegistry = Dict[int, RunControl]
