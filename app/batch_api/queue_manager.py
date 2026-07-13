"""BatchQueueManager — DB-polled bounded-concurrency executor for batch runs.

The queue *is* the batch_runs table: submit sets status='queued', workers poll
claim_next_queued() (priority DESC, created_at ASC) and atomically flip the row
to 'running'. Queue order and pending work survive a process restart with no
in-memory state to lose.
"""
import os
import threading
import time
from typing import Set, Optional

from .models import BatchRequest, CalibrationRequest, Tool
from .orchestrator import BatchOrchestrator
from ..core.db.connection import get_connection
from ..core.db.repositories.batch import BatchRepository
from ..core.config import QUEUE_POLL_INTERVAL_S, DISK_BACKPRESSURE_PCT, DISK_BACKPRESSURE_POLL_S
from ..core.logger import get_logger

log = get_logger(__name__)


class BatchQueueManager:
    def __init__(self, orchestrator: BatchOrchestrator, max_workers: int = 1):
        self.orchestrator = orchestrator
        self.max_workers = max_workers
        self.repo = BatchRepository()
        self._threads: list[threading.Thread] = []
        self._running_jobs: Set[int] = set()
        self._lock = threading.Lock()
        self._stopped = False

    def start(self) -> None:
        self._stopped = False
        self._threads = []
        for i in range(self.max_workers):
            t = threading.Thread(target=self._worker_loop, name=f"BatchQueueWorker-{i+1}", daemon=True)
            self._threads.append(t)
            t.start()
        log.info(f"Started BatchQueueManager (DB-poll) with {self.max_workers} worker(s).")

    def stop(self, grace_s: float = 5.0) -> None:
        log.info("Stopping BatchQueueManager (grace=%.1fs)...", grace_s)
        self._stopped = True
        with self._lock:
            for run_id in list(self._running_jobs):
                ctrl = self.orchestrator.run_controls.get(run_id)
                if ctrl:
                    log.info(f"Cancelling in-flight run_id={run_id} during shutdown.")
                    ctrl.cancel()
        for t in self._threads:
            t.join(timeout=grace_s)
        log.info("BatchQueueManager stopped.")

    def submit_batch(self, run_id: int, request: BatchRequest) -> None:
        """Mark a run queued. Workers pick it up by priority via DB poll."""
        if self._stopped:
            raise RuntimeError("Cannot submit to a stopped queue manager.")
        with get_connection() as conn:
            self.repo.update_status(conn, run_id, "queued")
        self._refresh_queue_depth()
        log.info(f"Queued batch run_id={run_id}.")

    def submit_calibration(self, run_id: int, request: CalibrationRequest) -> None:
        if self._stopped:
            raise RuntimeError("Cannot submit to a stopped queue manager.")
        with get_connection() as conn:
            self.repo.update_status(conn, run_id, "queued")
        self._refresh_queue_depth()
        log.info(f"Queued calibration run_id={run_id}.")

    def resume_queued_jobs(self) -> None:
        """No-op in DB-poll queue manager (automatically picked up on next poll)."""
        pass

    def _disk_backpressure_wait(self, target_dir: str) -> None:
        """Block while the target volume is over the disk-% threshold (e5.3)."""
        from .image_guards import disk_pct_over_threshold
        while not self._stopped and disk_pct_over_threshold(target_dir, DISK_BACKPRESSURE_PCT):
            log.warning("Disk backpressure: %s over %.0f%%; pausing pickup.", target_dir, DISK_BACKPRESSURE_PCT)
            time.sleep(DISK_BACKPRESSURE_POLL_S)

    def _reconstruct_request(self, row: dict):
        category_list = [c for c in row["category"].split(",") if c] if row.get("category") else ["general"]
        sample_val = row.get("sample")
        input_files_list = [f for f in row["input_files"].split(",") if f] if row.get("input_files") else None
        if row["trigger_type"] == "calibration":
            return CalibrationRequest(
                source_dir=row["source_dir"],
                target_format=[f for f in row["target_format"].split(",") if f],
                tool=[Tool(t) for t in row["tool"].split(",") if t],
                category=category_list,
                sample=sample_val if sample_val is not None else 30,
                target_ssim=0.98,
                regenerate_table=True,
            )
        return BatchRequest(
            source_dir=row["source_dir"],
            target_dir=row["target_dir"],
            target_format=[f for f in row["target_format"].split(",") if f],
            tool=[Tool(t) for t in row["tool"].split(",") if t],
            category=category_list,
            trigger_type=row["trigger_type"],
            input_files=input_files_list,
            sample=sample_val,
        )

    def _refresh_queue_depth(self) -> None:
        try:
            from .metrics import set_queue_depth
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) AS n FROM batch_runs WHERE status = 'queued'")
                set_queue_depth(int(cur.fetchone()["n"]))
        except Exception:
            pass

    def _worker_loop(self) -> None:
        while not self._stopped:
            try:
                self._refresh_queue_depth()
                claimed = self.repo.claim_next_queued(get_connection)
                if claimed is None:
                    time.sleep(QUEUE_POLL_INTERVAL_S)
                    continue
                run_id = claimed["id"]
                self._disk_backpressure_wait(claimed["target_dir"])
                if self._stopped:
                    # Return the row to the queue so a restart re-runs it.
                    with get_connection() as conn:
                        self.repo.update_status(conn, run_id, "queued")
                    break
                with self._lock:
                    self._running_jobs.add(run_id)
                try:
                    request = self._reconstruct_request(claimed)
                    if isinstance(request, CalibrationRequest):
                        from .run_control import RunControl
                        from .calibration_runner import run_calibration
                        from app.core import config
                        # Scope the calibration write-gate to this run only so
                        # subsequent normal batches don't silently record
                        # calibration rows (bd-qk1.2).
                        prev_calibration_enabled = config.CALIBRATION_ENABLED
                        config.CALIBRATION_ENABLED = True
                        try:
                            ctrl = self.orchestrator.run_controls.setdefault(run_id, RunControl())
                            log.info(f"Starting calibration run_id={run_id}")
                            run_calibration(
                                request.source_dir,
                                request.category,
                                [t.value for t in request.tool],
                                list(request.target_format),
                                sample=request.sample,
                                target_ssim=request.target_ssim,
                                regenerate_table=request.regenerate_table,
                                run_id=run_id,
                                run_control=ctrl,
                            )
                        finally:
                            config.CALIBRATION_ENABLED = prev_calibration_enabled
                    else:
                        log.info(f"Executing run_id={run_id} (priority={claimed['priority']}).")
                        self.orchestrator.execute_batch(run_id, request)
                except Exception as e:
                    log.error(f"Error executing run_id={run_id}: {e}", exc_info=True)
                finally:
                    self.orchestrator.run_controls.pop(run_id, None)
                    with self._lock:
                        self._running_jobs.discard(run_id)
            except Exception as e:
                log.error(f"Worker loop error: {e}", exc_info=True)
                time.sleep(QUEUE_POLL_INTERVAL_S)


_global_queue_manager: Optional[BatchQueueManager] = None


def init_queue_manager(orchestrator: BatchOrchestrator) -> BatchQueueManager:
    global _global_queue_manager
    max_workers = int(os.getenv("PIXELPIVOT_MAX_CONCURRENT_BATCHES", "1"))
    _global_queue_manager = BatchQueueManager(orchestrator, max_workers=max_workers)
    _global_queue_manager.start()
    return _global_queue_manager


def get_queue_manager() -> BatchQueueManager:
    global _global_queue_manager
    if _global_queue_manager is None:
        raise RuntimeError("BatchQueueManager has not been initialized.")
    return _global_queue_manager
