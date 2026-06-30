"""Hot folder management and file monitoring system.

Watches directories for new images and triggers batches after debounce.
Uses watchdog for cross-platform file event detection and polling fallback
for networked/slow filesystems.
"""
import threading
import asyncio
import os
import time
from pathlib import Path
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer
from typing import Dict, Any, List

from .models import BatchRequest, HotFolderRequest
from ..core.logger import get_logger
from ..core import tracing
from ..core.db.repositories.batch import BatchRepository
from ..core.db.connection import get_connection
from ..core.config import (
    HOT_FOLDER_READINESS_TIMEOUT_MS,
    HOT_FOLDER_READINESS_CHECK_MS,
    HOT_FOLDER_POLLING_INTERVAL_S,
    HOT_FOLDER_DEBOUNCE_MS,
)

log = get_logger(__name__)

class HotFolderHandler(FileSystemEventHandler):
    """Listens for file events in a hot folder and triggers batch jobs.

    Uses debouncing to group multiple file events into a single batch,
    and readiness checks to ensure files are fully written before conversion.
    """
    def __init__(self, orchestrator, loop: asyncio.AbstractEventLoop,
                 config: Dict[str, Any], debounce_seconds: float = 5.0):
        """Initialize hot folder handler.

        Args:
            orchestrator: BatchOrchestrator instance.
            loop: AsyncIO event loop for async task dispatch.
            config: Hot folder configuration dict.
            debounce_seconds: Grace period before triggering batch (default 5.0).
        """
        self.orchestrator = orchestrator
        self.loop = loop
        self.config = config
        self.debounce_seconds = debounce_seconds
        self.timer = None
        self.lock = threading.Lock()
        self.repo = BatchRepository()
        self._is_triggering = False
        self.processed_files = set()

    def on_created(self, event):
        """Handle file creation event."""
        if event.is_directory:
            return
        log.debug(f"HotFolder detected new file: {event.src_path}")
        self._reset_timer()

    def on_modified(self, event):
        """Handle file modification event."""
        if event.is_directory:
            return
        log.debug(f"HotFolder detected modified file: {event.src_path}")
        self._reset_timer()

    def on_moved(self, event):
        """Handle file move event."""
        if event.is_directory:
            return
        log.debug(f"HotFolder detected moved file: {event.dest_path}")
        self._reset_timer()

    def _reset_timer(self):
        """Cancel pending debounce timer and restart it."""
        with self.lock:
            if self.timer:
                self.timer.cancel()
            self.timer = threading.Timer(self.debounce_seconds, self._trigger_batch)
            self.timer.daemon = True
            self.timer.start()

    def cancel_timer(self):
        """Cancel the pending debounce timer."""
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None

    def _stamp_trace(self):
        tracing.new_trace_id("hotfolder-")

    def _trigger_batch(self):
        """Initiate batch execution after debounce expires; guard against re-entrancy."""
        self._stamp_trace()
        with self.lock:
            if self._is_triggering:
                log.debug(f"Batch trigger already in progress for {self.config['source_dir']}. Skipping.")
                return
            self._is_triggering = True

        log.info(f"Debounce expired for {self.config['source_dir']}. Starting readiness checks.")
        
        # Dispatch async readiness check and execution
        fut = asyncio.run_coroutine_threadsafe(
            self._async_trigger_batch(),
            self.loop,
        )
        
        def _on_done(f):
            try:
                f.result()
            except Exception as e:
                log.error(f"Hot folder async task failed: {e}", exc_info=True)
                with self.lock:
                    self._is_triggering = False
        
        fut.add_done_callback(_on_done)

    async def _async_trigger_batch(self):
        """Perform readiness checks and dispatch batch to orchestrator."""
        try:
            source_dir = Path(self.config["source_dir"])
            
            # 1. Wait for file readiness
            if not await self._wait_for_readiness(source_dir):
                log.warning(f"Hot folder batch cancelled: files in {source_dir} never stabilized.")
                return

            # Find new/changed files to process
            valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".heic", ".heif", ".avif"}
            all_files = list(source_dir.glob("*"))
            
            files_to_process = []
            for f in all_files:
                if not f.is_file() or f.suffix.lower() not in valid_exts:
                    continue
                try:
                    st = f.stat()
                    file_key = (str(f), st.st_mtime, st.st_size)
                    
                    # Check in-memory processed set
                    if file_key in self.processed_files:
                        continue
                        
                    # Check output existence/mtime
                    already_converted = True
                    target_dir = Path(self.config["target_dir"])
                    suffix = self.config.get("suffix", "")
                    
                    target_formats = self.config["target_format"]
                    if isinstance(target_formats, str):
                        target_formats = [target_formats]
                    
                    for fmt in target_formats:
                        out_name = f"{f.stem}{suffix}.{fmt}"
                        out_path = target_dir / out_name
                        if not out_path.exists() or out_path.stat().st_mtime < st.st_mtime:
                            already_converted = False
                            break
                            
                    if already_converted:
                        self.processed_files.add(file_key)
                        continue
                        
                    files_to_process.append((str(f), file_key))
                except OSError:
                    pass

            if not files_to_process:
                log.info(f"No new or changed files detected in {source_dir}. Skipping batch trigger.")
                return

            input_paths = [path for path, _ in files_to_process]
            file_keys = [key for _, key in files_to_process]

            # 2. Create DB entry
            try:
                db_formats = ",".join(self.config["target_format"]) if isinstance(self.config["target_format"], list) else self.config["target_format"]
                db_tools = ",".join(self.config["tool"]) if isinstance(self.config["tool"], list) else self.config["tool"]
                
                with get_connection() as conn:
                    run_id = self.repo.create_run(
                        conn,
                        source_dir=self.config["source_dir"],
                        target_dir=self.config["target_dir"],
                        target_format=db_formats,
                        tool=db_tools,
                        trigger_type="hot_folder",
                        heuristic_version=self.orchestrator.interpolator.version
                    )
                
                # 3. Prepare request
                request = BatchRequest(
                    source_dir=self.config["source_dir"],
                    target_dir=self.config["target_dir"],
                    target_format=self.config["target_format"],
                    tool=self.config["tool"],
                    category=self.config.get("category", ["general"]),
                    trigger_type="hot_folder",
                    input_files=input_paths
                )
                
                # 4. Dispatch to orchestrator (sync → thread pool to avoid blocking event loop)
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(
                    None, self.orchestrator.execute_batch, run_id, request
                )
                
                # Mark as processed after dispatch
                for key in file_keys:
                    self.processed_files.add(key)
                    
            except Exception as e:
                log.error(f"Failed to trigger hot folder batch: {e}")
        finally:
            with self.lock:
                self._is_triggering = False

    async def _wait_for_readiness(self, source_dir: Path) -> bool:
        """Poll file sizes until stable or timeout.

        Args:
            source_dir: Directory to monitor.

        Returns:
            True if files stabilized, False on timeout or no files.
        """
        timeout_s = HOT_FOLDER_READINESS_TIMEOUT_MS / 1000.0
        check_interval_s = HOT_FOLDER_READINESS_CHECK_MS / 1000.0
        start_time = time.time()
        
        last_sizes: Dict[str, int] = {}
        
        while (time.time() - start_time) < timeout_s:
            current_files = list(source_dir.glob("*"))
            if not current_files:
                return False
                
            current_sizes = {}
            stable = True
            
            for f in current_files:
                if not f.is_file():
                    continue
                try:
                    size = os.path.getsize(f)
                    current_sizes[str(f)] = size
                    if last_sizes.get(str(f)) != size:
                        stable = False
                except OSError:
                    stable = False # File might be locked or deleted
            
            if stable and last_sizes:
                # All files are stable and we've checked at least twice
                return True
                
            last_sizes = current_sizes
            await asyncio.sleep(check_interval_s)
            
        return False

import uuid

class HotFolderManager:
    """Manages multiple hot folder watchers using watchdog and polling fallback."""

    def __init__(self, orchestrator, loop: asyncio.AbstractEventLoop):
        """Initialize hot folder manager.

        Args:
            orchestrator: BatchOrchestrator instance.
            loop: AsyncIO event loop for dispatch.
        """
        self.orchestrator = orchestrator
        self.loop = loop
        self.observer = Observer()
        self.watchers: Dict[str, Dict[str, Any]] = {}  # watcher_id -> {handler, watch, config, last_snapshot}
        self.is_running = True

        # Start polling thread (Task 11)
        self.polling_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self.polling_thread.start()

    def add_hot_folder(self, config: Dict[str, Any]) -> str:
        """Register a new hot folder for automatic monitoring.

        Args:
            config: Hot folder configuration dict.

        Returns:
            Unique watcher_id.

        Raises:
            ValueError: On invalid configuration or directory issues.
        """
        from pathlib import Path
        cfg = HotFolderRequest(**config)             # eager enum/type validation
        src = Path(cfg.source_dir).resolve()
        tgt = Path(cfg.target_dir).resolve()
        
        if src == tgt:
            raise ValueError("Source directory and target directory cannot be the same.")
            
        if not src.is_dir():
            raise ValueError(f"source_dir does not exist or is not a directory: {src}")

        cfg_dict = cfg.model_dump()
        # Task 025: thread the configured debounce window through to the
        # handler. Read from the module's binding (not the import alias) so
        # tests that monkeypatch HOT_FOLDER_DEBOUNCE_MS see the new value
        # without a re-import.
        handler = HotFolderHandler(
            self.orchestrator,
            self.loop,
            cfg_dict,
            debounce_seconds=HOT_FOLDER_DEBOUNCE_MS / 1000.0,
        )
        watch = self.observer.schedule(handler, str(src), recursive=False)
        watcher_id = uuid.uuid4().hex
        self.watchers[watcher_id] = {
            "handler": handler,
            "watch": watch,
            "config": cfg_dict,
            "last_snapshot": set()
        }
        log.info(f"Added hot folder: {src} -> {cfg.target_format} ({cfg.tool})")
        return watcher_id

    def remove_hot_folder(self, watcher_id: str) -> bool:
        """Stop and unregister a hot folder watcher.

        Args:
            watcher_id: Unique watcher identifier.

        Returns:
            True if removed, False if not found.
        """
        entry = self.watchers.pop(watcher_id, None)
        if not entry:
            return False
        entry["handler"].cancel_timer()
        self.observer.unschedule(entry["watch"])
        return True

    def list_hot_folders(self) -> List[Dict[str, Any]]:
        """Retrieve all active hot folder configurations.

        Returns:
            List of watcher configs with watcher_id.
        """
        return [
            {"watcher_id": wid, **entry["config"]}
            for wid, entry in self.watchers.items()
        ]

    def start(self):
        """Start the watchdog observer."""
        log.info("Starting Hot Folder Watchdog...")
        self.observer.start()

    def stop(self):
        """Stop the watchdog observer and cancel all pending timers."""
        log.info("Stopping Hot Folder Watchdog and cancelling active timers...")
        self.is_running = False
        for entry in self.watchers.values():
            entry["handler"].cancel_timer()
        self.observer.stop()
        if self.observer.is_alive():
            self.observer.join()

    def _poll_loop(self):
        """Background loop for networked drive polling fallback."""
        while self.is_running:
            try:
                time.sleep(HOT_FOLDER_POLLING_INTERVAL_S)
                for wid, info in list(self.watchers.items()):
                    self._poll_watcher(info)
            except Exception as e:
                log.error(f"Hot folder polling loop error: {e}")

    def _poll_watcher(self, info: Dict[str, Any]):
        """Scan directory and compare with last snapshot; trigger if changed.

        Args:
            info: Watcher entry from self.watchers.
        """
        try:
            src = Path(info["config"]["source_dir"])
            if not src.exists():
                return
                
            # Snapshot: (name, mtime, size)
            current_snapshot = set()
            for f in src.glob("*"):
                if f.is_file():
                    try:
                        st = f.stat()
                        current_snapshot.add((f.name, st.st_mtime, st.st_size))
                    except OSError:
                        pass
            
            if current_snapshot != info["last_snapshot"]:
                if info["last_snapshot"]: # Only trigger if not first scan
                    log.debug(f"Polling detected change in {src}. Triggering handler.")
                    info["handler"]._reset_timer()
                info["last_snapshot"] = current_snapshot

        except Exception as e:
            log.debug(f"Polling failed for {info['config']['source_dir']}: {e}")

# Global instance to be initialized by main.py
_manager: "HotFolderManager | None" = None

def init_hot_folder_manager(orchestrator, loop: asyncio.AbstractEventLoop) -> HotFolderManager:
    global _manager
    _manager = HotFolderManager(orchestrator, loop)
    return _manager

def get_hot_folder_manager() -> HotFolderManager:
    if _manager is None:
        raise RuntimeError("HotFolderManager not initialized.")
    return _manager
