"""FastAPI REST endpoints for batch jobs and hot folder management.

Exposes /api/v1 routes for:
- Batch execution (/batch/start, /batch/status, /batch/{id}/errors, /batch/history)
- Hot folder management (/hotfolder/register, /hotfolder/list, /hotfolder/{id})
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from .models import BatchRequest, BatchStatusResponse, HotFolderRequest
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
async def start_batch(
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
        with get_connection() as conn:
            run_id = repo.create_run(
                conn,
                source_dir=req.source_dir,
                target_dir=req.target_dir,
                target_format=",".join(req.target_format),
                tool=",".join([t.value for t in req.tool]),
                trigger_type=req.trigger_type,
                heuristic_version=orchestrator.interpolator.version
            )

        bg_tasks.add_task(orchestrator.execute_batch, run_id, req)
        
        return {"run_id": run_id, "status": "queued"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/batch/status/{run_id}")
async def get_batch_status(run_id: int):
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
            
        return {
            "run_id": run["id"],
            "status": run["status"],
            "total_images": run["total_images"],
            "created_at": run["created_at"],
            "completed_at": run["completed_at"],
            "summary": summary
        }

@router.get("/batch/{run_id}/errors")
async def get_batch_errors(run_id: int):
    """Retrieve error records for a batch run.

    Args:
        run_id: Unique batch run identifier.

    Returns:
        List of error dicts, each with path and error message.
    """
    with get_connection() as conn:
        return repo.get_errors(conn, run_id)

@router.get("/batch/history")
async def get_batch_history():
    """Retrieve all completed and running batch runs.

    Returns:
        List of batch run records from database.
    """
    with get_connection() as conn:
        runs = repo.get_all_runs(conn)
        return runs

@router.post("/hotfolder/register")
async def register_hot_folder(req: HotFolderRequest):
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
async def list_hot_folders():
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
async def unregister_hot_folder(watcher_id: str):
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
