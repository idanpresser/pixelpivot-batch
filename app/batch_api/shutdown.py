# app/batch_api/shutdown.py
"""Graceful-shutdown coordinator for SIGTERM (driven by uvicorn's lifespan).

Order matters:
  1. Stop the hot-folder lane FIRST so no new debounced batch can enqueue while
     we are draining.
  2. Drain the worker lane: queue_manager.stop() signals cooperative cancel; the
     active matrix chunk finishes via the orchestrator's between-cell
     ctrl.cancelled checks, then worker threads join within the grace window.
  3. terminate()/kill() any child process that outlived its joined thread so no
     orphan ffmpeg/mogrify survives holding FDs or leaving partial output.
"""
from __future__ import annotations

from ..core.logger import get_logger
from ..core import process_registry as _default_registry

log = get_logger(__name__)


def graceful_shutdown(hot_folder_manager, queue_manager, grace_s, registry=_default_registry) -> int:
    """Drain both lanes within the grace window, then reap surviving children.

    Returns the number of child processes that had to be force-signalled.
    """
    if hot_folder_manager is not None:
        try:
            hot_folder_manager.stop()
        except Exception as e:
            log.warning("hot folder stop failed during shutdown: %s", e)

    if queue_manager is not None:
        try:
            queue_manager.stop(grace_s=grace_s)
        except Exception as e:
            log.warning("queue manager stop failed during shutdown: %s", e)

    try:
        killed = registry.terminate_all(grace_s=grace_s)
    except Exception as e:
        log.error("process registry terminate_all failed during shutdown: %s", e)
        killed = 0

    if killed:
        log.warning("graceful shutdown force-signalled %d surviving child process(es).", killed)
    return killed
