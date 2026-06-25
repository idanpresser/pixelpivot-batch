"""BatchQueueManager — coordinates queueing and bounded concurrency for batch runs.

Replaces BackgroundTasks by executing batch runs in a dedicated, thread-safe queue
with a configurable concurrency cap (max_workers).
"""
import os
import queue
import threading
from typing import Set, Dict, Any, Optional

from .models import BatchRequest, CalibrationRequest, Tool
from .orchestrator import BatchOrchestrator
from ..core.db.connection import get_connection
from ..core.db.repositories.batch import BatchRepository
from ..core.logger import get_logger

log = get_logger(__name__)

class BatchQueueManager:
    """Manages batch runs in a bounded concurrent queue.

    Enforces that at most max_workers execute concurrently. Other runs wait in the queue.
    """
    def __init__(self, orchestrator: BatchOrchestrator, max_workers: int = 1):
        self.orchestrator = orchestrator
        self.max_workers = max_workers
        self.queue: queue.Queue[Optional[tuple[int, BatchRequest]]] = queue.Queue()
        self.repo = BatchRepository()
        self._threads: list[threading.Thread] = []
        self._running_jobs: Set[int] = set()
        self._lock = threading.Lock()
        self._stopped = False

    def start(self) -> None:
        """Start the background worker threads."""
        self._stopped = False
        self._threads = []
        for i in range(self.max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"BatchQueueWorker-{i+1}",
                daemon=True
            )
            self._threads.append(t)
            t.start()
        log.info(f"Started BatchQueueManager with {self.max_workers} worker thread(s).")

    def stop(self) -> None:
        """Gracefully stop the queue manager, cancelling in-flight jobs and waiting for workers."""
        log.info("Stopping BatchQueueManager...")
        self._stopped = True
        
        # Signal all worker threads to stop by putting None sentinels
        for _ in range(self.max_workers):
            self.queue.put(None)

        # Signal cancellation to any active runs
        with self._lock:
            for run_id in list(self._running_jobs):
                ctrl = self.orchestrator.run_controls.get(run_id)
                if ctrl:
                    log.info(f"Cancelling in-flight run_id={run_id} during queue manager shutdown.")
                    ctrl.cancel()

        # Join the threads with a timeout
        for t in self._threads:
            t.join(timeout=5.0)
        log.info("BatchQueueManager stopped.")

    def submit_batch(self, run_id: int, request: BatchRequest) -> None:
        """Submit a batch run to the queue.

        Updates status in database to 'queued' on submission.
        """
        if self._stopped:
            raise RuntimeError("Cannot submit to a stopped queue manager.")
        
        # Update database status to 'queued'
        with get_connection() as conn:
            self.repo.update_status(conn, run_id, "queued")

        self.queue.put((run_id, request))
        log.info(f"Queued batch run_id={run_id} for background processing.")

    def submit_calibration(self, run_id: int, request: "CalibrationRequest") -> None:
        """Submit an offline calibration run to the same bounded queue."""
        if self._stopped:
            raise RuntimeError("Cannot submit to a stopped queue manager.")
        with get_connection() as conn:
            self.repo.update_status(conn, run_id, "queued")
        self.queue.put((run_id, request))
        log.info(f"Queued calibration run_id={run_id}.")

    def resume_queued_jobs(self) -> None:
        """Scan database for any 'queued' runs and enqueue them for resume on restart."""
        try:
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, source_dir, target_dir, target_format, tool, trigger_type FROM batch_runs WHERE status = 'queued' ORDER BY id ASC"
                )
                rows = cur.fetchall()
                if not rows:
                    return
                
                log.info(f"Found {len(rows)} previously queued batch(es) to resume.")
                for row in rows:
                    run_id = row["id"]
                    try:
                        # Reconstruct BatchRequest
                        request = BatchRequest(
                            source_dir=row["source_dir"],
                            target_dir=row["target_dir"],
                            target_format=[f for f in row["target_format"].split(",") if f],
                            tool=[Tool(t) for t in row["tool"].split(",") if t],
                            category=["general"],
                            trigger_type=row["trigger_type"],
                        )
                        # We put directly in queue to avoid re-writing status in submit_batch
                        self.queue.put((run_id, request))
                        log.info(f"Resumed queued run_id={run_id}")
                    except Exception as e:
                        log.error(f"Failed to resume queued run_id={run_id}: {e}")
        except Exception as e:
            log.error(f"Error checking for queued jobs to resume: {e}")

    def _worker_loop(self) -> None:
        """Main loop executed by worker threads."""
        while not self._stopped:
            try:
                item = self.queue.get()
                if item is None:
                    # Sentinel received, exit thread
                    self.queue.task_done()
                    break

                run_id, request = item
                
                # Check database to see if job was cancelled while in queue
                with get_connection() as conn:
                    run = self.repo.get_run(conn, run_id)
                    if not run or run["status"] == "cancelled":
                        log.info(f"Skipping run_id={run_id} as it was cancelled or removed before starting.")
                        self.queue.task_done()
                        continue
                    
                    # Update status to 'running'
                    self.repo.update_status(conn, run_id, "running")

                with self._lock:
                    self._running_jobs.add(run_id)

                try:
                    if isinstance(request, CalibrationRequest):
                        from .run_control import RunControl
                        from .calibration_runner import run_calibration
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
                    else:
                        log.info(f"Starting execution of run_id={run_id}")
                        self.orchestrator.execute_batch(run_id, request)
                except Exception as e:
                    log.error(f"Error executing run_id={run_id}: {e}", exc_info=True)
                finally:
                    self.orchestrator.run_controls.pop(run_id, None)
                    with self._lock:
                        self._running_jobs.discard(run_id)
                    self.queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                log.error(f"Error in BatchQueueWorker loop: {e}", exc_info=True)


_global_queue_manager: Optional[BatchQueueManager] = None

def init_queue_manager(orchestrator: BatchOrchestrator) -> BatchQueueManager:
    """Initialize and start the global BatchQueueManager."""
    global _global_queue_manager
    max_workers = int(os.getenv("PIXELPIVOT_MAX_CONCURRENT_BATCHES", "1"))
    _global_queue_manager = BatchQueueManager(orchestrator, max_workers=max_workers)
    _global_queue_manager.start()
    _global_queue_manager.resume_queued_jobs()
    return _global_queue_manager

def get_queue_manager() -> BatchQueueManager:
    """Get the active global BatchQueueManager."""
    global _global_queue_manager
    if _global_queue_manager is None:
        raise RuntimeError("BatchQueueManager has not been initialized.")
    return _global_queue_manager
