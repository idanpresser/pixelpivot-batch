"""FastAPI application entry point for PixelPivot Batch Engine.

Initializes the FastAPI app with startup/shutdown lifespan handlers,
mounts REST API routes, and manages the BatchOrchestrator and HotFolderManager.
"""
import asyncio
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from .routes import router
from .hot_folder import init_hot_folder_manager, get_hot_folder_manager
from .orchestrator import BatchOrchestrator
from ..core.db.schema import init_db
from ..core.config import MIN_PYTHON_VERSION
from ..core.logger import get_logger

log = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage FastAPI app lifecycle: startup initialization and shutdown cleanup."""
    from .security import check_security_config
    check_security_config()

    # Fail loudly on a wrong Python (air-gap deploy onto a host below the
    # declared floor) here at lifespan, not cryptically at native-wheel import.
    if sys.version_info[:2] < MIN_PYTHON_VERSION:
        msg = (
            f"Python {MIN_PYTHON_VERSION[0]}.{MIN_PYTHON_VERSION[1]}+ required; "
            f"running {sys.version_info.major}.{sys.version_info.minor}. "
            "Vendored native wheels are ABI-pinned to the declared floor."
        )
        log.error(msg)
        raise RuntimeError(msg)

    if sys.platform == "win32":
        try:
            import winreg
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SYSTEM\CurrentControlSet\Control\FileSystem",
                    0,
                    winreg.KEY_READ
                )
                value, _ = winreg.QueryValueEx(key, "LongPathsEnabled")
                winreg.CloseKey(key)
                if value != 1:
                    msg = "Windows LongPathsEnabled registry key is disabled. Long path support is required. Please enable it in registry HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled."
                    log.error(msg)
                    raise RuntimeError(msg)
            except FileNotFoundError:
                msg = "Windows LongPathsEnabled registry key not found. Long path support is required. Please enable it in registry HKLM\\SYSTEM\\CurrentControlSet\\Control\\FileSystem\\LongPathsEnabled."
                log.error(msg)
                raise RuntimeError(msg)
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            log.warning("Could not query Windows LongPathsEnabled registry key: %s. Proceeding with caution.", e)

    # Schema bootstrap — idempotent. SQLite file is created on first connect.
    try:
        init_db()
    except Exception as e:
        log.error("init_db failed on startup: %s", e)
        raise

    # Reap orphaned 'running' batches left by a prior crash/restart so they do
    # not linger forever in /batch/status. Best-effort: never block startup.
    try:
        from ..core.db.connection import get_connection
        from ..core.db.repositories.batch import BatchRepository
        with get_connection() as conn:
            reaped = BatchRepository().reap_stale_running(conn)
        if reaped:
            log.warning("Reaped %d orphaned 'running' batch(es) on startup.", reaped)
    except Exception as e:
        log.error("Startup reaper failed: %s", e)

    loop = asyncio.get_running_loop()
    app.state.orchestrator = BatchOrchestrator()
    from .queue_manager import init_queue_manager
    app.state.queue_manager = init_queue_manager(app.state.orchestrator)

    # Eagerly start Sharp daemon on startup
    sharp_conv = app.state.orchestrator.converters.get("sharp")
    if sharp_conv:
        try:
            from ..core.toolcheck import check_sharp_install
            install_status = check_sharp_install()
            if install_status.ok:
                log.info("Eagerly starting Sharp Node daemon on startup...")
                sharp_conv._ensure_daemon_running()
            else:
                log.warning("Sharp Node daemon not started eagerly: %s", install_status.detail)
        except Exception as e:
            log.warning("Failed to start Sharp Node daemon eagerly on startup: %s", e)

    manager = init_hot_folder_manager(app.state.orchestrator, loop)
    manager.start()
    app.state.hot_folder_manager = manager
    try:
        yield
    finally:
        manager.stop()
        if hasattr(app.state, "queue_manager") and app.state.queue_manager:
            try:
                app.state.queue_manager.stop()
            except Exception as e:
                log.warning("Failed to stop BatchQueueManager on shutdown: %s", e)
        # Eagerly stop Sharp daemon on shutdown
        sharp_conv = getattr(app.state, "orchestrator", None) and app.state.orchestrator.converters.get("sharp")
        if sharp_conv:
            try:
                log.info("Eagerly stopping Sharp Node daemon on shutdown...")
                sharp_conv._stop_daemon()
            except Exception as e:
                log.warning("Failed to stop Sharp Node daemon on shutdown: %s", e)

app = FastAPI(title="PixelPivot Batch Engine", lifespan=lifespan)

from app.core import tracing

@app.middleware("http")
async def trace_id_middleware(request, call_next):
    tracing.new_trace_id("req-")
    return await call_next(request)

@app.middleware("http")
async def api_token_auth_middleware(request, call_next):
    import os
    token = os.environ.get("PIXELPIVOT_API_TOKEN")
    if token:
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            req_token = request.headers.get("X-API-Token")
            if not req_token or req_token != token:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Unauthorized: Invalid or missing X-API-Token header."}
                )
    return await call_next(request)

app.include_router(router, prefix="/api/v1")

@app.get("/")
async def root():
    """Return health check response."""
    return {"message": "PixelPivot Batch Engine API is running"}
