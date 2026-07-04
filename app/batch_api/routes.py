"""FastAPI REST endpoints for batch jobs and hot folder management.

Exposes /api/v1 routes for:
- Batch execution (/batch/start, /batch/status, /batch/{id}/errors, /batch/history)
- Hot folder management (/hotfolder/register, /hotfolder/list, /hotfolder/{id})
"""
import psutil
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from .models import BatchRequest, BatchStatusResponse, HotFolderRequest, ControlRequest, CalibrationRequest
from ..core.db.repositories.batch import BatchRepository
from .orchestrator import BatchOrchestrator
from ..core.db.connection import get_connection
from .hot_folder import get_hot_folder_manager

router = APIRouter()
repo = BatchRepository()

def get_orchestrator(request: Request) -> BatchOrchestrator:
    """Extract orchestrator instance from FastAPI app state."""
    return request.app.state.orchestrator

@router.post("/batch/start")
def start_batch(
    req: BatchRequest,
    bg_tasks: BackgroundTasks,
    orchestrator: BatchOrchestrator = Depends(get_orchestrator)
):
    """Initiate a batch job and queue it for background execution.

    Args:
        req: Batch request with source_dir, target_dir, formats, tools, categories.
        bg_tasks: FastAPI background task manager.
        orchestrator: Injected BatchOrchestrator instance.

    Returns:
        Dict with run_id (int) and status (str: "queued").

    Raises:
        HTTPException: On database or validation errors (500).
    """
    try:
        from ..core.config import PRIORITY_HIGH
        with get_connection() as conn:
            run_id = repo.create_run(
                conn,
                source_dir=req.source_dir,
                target_dir=req.target_dir,
                target_format=",".join(req.target_format),
                tool=",".join([t.value for t in req.tool]),
                trigger_type=req.trigger_type,
                heuristic_version=orchestrator.interpolator.version,
                priority=PRIORITY_HIGH,
                category=",".join(req.category),
                sample=req.sample,
                input_files=",".join(req.input_files) if req.input_files else None,
            )

        from .queue_manager import get_queue_manager
        get_queue_manager().submit_batch(run_id, req)
        
        return {"run_id": run_id, "status": "queued"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/calibrate")
def start_calibration(
    req: CalibrationRequest,
    orchestrator: BatchOrchestrator = Depends(get_orchestrator),
):
    """Queue an offline serial SSIM calibration run; regenerates priors on completion."""
    try:
        with get_connection() as conn:
            run_id = repo.create_run(
                conn,
                source_dir=req.source_dir,
                target_dir=req.source_dir,
                target_format=",".join(req.target_format),
                tool=",".join([t.value for t in req.tool]),
                trigger_type="calibration",
                heuristic_version=orchestrator.interpolator.version,
                category=",".join(req.category),
                sample=req.sample,
            )
        from .queue_manager import get_queue_manager
        get_queue_manager().submit_calibration(run_id, req)
        return {"run_id": run_id, "status": "queued"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/batch/status/{run_id}")
def get_batch_status(
    run_id: int,
    orchestrator: BatchOrchestrator = Depends(get_orchestrator)
):
    """Retrieve batch job status and summary metrics.

    Args:
        run_id: Unique batch run identifier.

    Returns:
        Dict with run_id, status, total_images, created_at, completed_at, and optional summary.

    Raises:
        HTTPException: 404 if batch not found, 500 on database errors.
    """
    with get_connection() as conn:
        run = repo.get_run(conn, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Batch run not found")
        
        summary = None
        if run["status"] == "completed":
            summary = repo.get_summary(conn, run_id)
            
        res = {
            "run_id": run["id"],
            "status": run["status"],
            "total_images": run["total_images"],
            "created_at": run["created_at"],
            "completed_at": run["completed_at"],
            "summary": summary
        }
        
        # Fold in live progress counters when in-flight and available in-memory
        if run["status"] not in ("completed", "failed", "cancelled"):
            state = orchestrator.progress.get(run_id)
            if state:
                for key in ("cells_done", "cells_total", "current_cell", "ok", "fail"):
                    if key in state:
                        res[key] = state[key]
                        
        return res

@router.get("/batch/{run_id}/errors")
def get_batch_errors(run_id: int):
    """Retrieve error records for a batch run.

    Args:
        run_id: Unique batch run identifier.

    Returns:
        List of error dicts, each with path and error message.
    """
    with get_connection() as conn:
        return repo.get_errors(conn, run_id)

@router.get("/batch/history")
def get_batch_history():
    """Retrieve all completed and running batch runs.

    Returns:
        List of batch run records from database.
    """
    with get_connection() as conn:
        runs = repo.get_all_runs(conn)
        return runs

@router.post("/hotfolder/register")
def register_hot_folder(req: HotFolderRequest):
    """Register a directory to be monitored for automatic batch processing.

    Args:
        req: Hot folder config with source_dir, target_dir, formats, tools, category.

    Returns:
        Dict with watcher_id (str) and status (str: "active").

    Raises:
        HTTPException: 400 on validation errors, 500 on system errors.
    """
    try:
        manager = get_hot_folder_manager()
        watcher_id = manager.add_hot_folder(req.model_dump())
        return {"watcher_id": watcher_id, "status": "active"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/hotfolder/list")
def list_hot_folders():
    """Retrieve all active hot folder watchers.

    Returns:
        List of watcher configs with source_dir, target_dir, formats, tools.
    """
    try:
        manager = get_hot_folder_manager()
        return manager.list_hot_folders()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/hotfolder/{watcher_id}")
def unregister_hot_folder(watcher_id: str):
    """Stop and unregister a hot folder watcher.

    Args:
        watcher_id: Unique watcher identifier.

    Returns:
        Dict with status (str: "removed").

    Raises:
        HTTPException: 404 if watcher not found.
    """
    manager = get_hot_folder_manager()
    if not manager.remove_hot_folder(watcher_id):
        raise HTTPException(status_code=404, detail="Watcher not found")
    return {"status": "removed"}


@router.post("/batch/{run_id}/control")
def control_batch(
    run_id: int,
    req: ControlRequest,
    orchestrator: BatchOrchestrator = Depends(get_orchestrator),
):
    """Pause, resume, or stop an in-flight batch run."""
    ctrl = orchestrator.run_controls.get(run_id)
    if ctrl is None:
        raise HTTPException(status_code=404, detail="No active run with that id")
    if req.action == "pause":
        ctrl.pause()
        new_status = "paused"
    elif req.action == "resume":
        ctrl.resume()
        new_status = "running"
    else:  # stop
        ctrl.cancel()
        new_status = None  # orchestrator marks 'cancelled' when the loop exits
    if new_status is not None:
        with get_connection() as conn:
            repo.update_status(conn, run_id, new_status)
    return {"run_id": run_id, "action": req.action}


@router.post("/batch/{run_id}/restart")
def restart_batch(
    run_id: int,
    bg_tasks: BackgroundTasks,
    orchestrator: BatchOrchestrator = Depends(get_orchestrator),
):
    """Re-run a finished batch using its originally stored configuration.

    Note: category is not persisted on batch_runs, so a restart re-runs with
    the default category ['general'].
    """
    from .models import BatchRequest, Tool
    with get_connection() as conn:
        run = repo.get_run(conn, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Batch run not found")
        new_req = BatchRequest(
            source_dir=run["source_dir"],
            target_dir=run["target_dir"],
            target_format=[f for f in run["target_format"].split(",") if f],
            tool=[Tool(t) for t in run["tool"].split(",") if t],
            category=[c for c in run["category"].split(",") if c] if run.get("category") else ["general"],
            trigger_type="restart",
            sample=run.get("sample"),
            input_files=[f for f in run["input_files"].split(",") if f] if run.get("input_files") else None,
        )
        new_id = repo.create_run(
            conn,
            source_dir=new_req.source_dir,
            target_dir=new_req.target_dir,
            target_format=",".join(new_req.target_format),
            tool=",".join([t.value for t in new_req.tool]),
            trigger_type="restart",
            heuristic_version=orchestrator.interpolator.version,
            category=",".join(new_req.category),
            sample=new_req.sample,
            input_files=",".join(new_req.input_files) if new_req.input_files else None,
        )
    from .queue_manager import get_queue_manager
    get_queue_manager().submit_batch(new_id, new_req)
    return {"run_id": new_id, "status": "queued"}


@router.get("/batch/{run_id}/progress")
def get_batch_progress(
    run_id: int,
    orchestrator: BatchOrchestrator = Depends(get_orchestrator),
):
    """Return live in-flight progress for a running batch plus a resource sample."""
    state = orchestrator.progress.get(run_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No live progress for that run")
    sample = {
        "cpu_pct": psutil.cpu_percent(interval=None),
        "ram_mb": round(psutil.virtual_memory().used / (1024 * 1024), 1),
    }
    return {**state, **sample}

