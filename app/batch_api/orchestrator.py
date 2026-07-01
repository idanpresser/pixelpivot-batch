"""BatchOrchestrator — coordinates image conversion tasks across multiple tools.

Executes batch jobs using a matrix of (category, tool, format) combinations.
Manages heuristic quality interpolation, resource preflight checks, and disk
space monitoring. Writes aggregated metrics to batch_summary on completion.
"""
import os
import time
import sqlite3
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Any, Callable, TypeVar, Tuple, Optional

from .models import BatchRequest, Tool
from .run_control import RunControl, RunControlRegistry
from ..core.db.repositories.batch import BatchRepository
from ..core.db.connection import get_connection, with_db_retry
from ..core.heuristic_interpolator import HeuristicInterpolator
from ..core.converters.magick_converter import MagickConverter
from ..core.converters.ffmpeg_converter import FFmpegConverter
from ..core.converters.vips_converter import VipsConverter
from ..core.converters.sharp_converter import SharpConverter
from ..core.logger import get_logger
from ..core.config import (
    FFMPEG_TIMEOUT,
    HEURISTIC_TABLE_PATH,
    MAGICK_MOGRIFY_CHUNK,
    SQLITE_BUSY_ATTEMPTS,
    SQLITE_BUSY_BASE_DELAY_S,
    DISK_RECHECK_EVERY_CELLS,
    default_quality_for,
)
from ..core.paths import APP_ROOT, PROJ_ROOT

log = get_logger(__name__)

T = TypeVar("T")


def quarantine_to_dlq(in_path: str, target_dir: str, reason: str) -> dict:
    """Move a failed input into <target_dir>/corrupt_or_failed/ and return a batch_errors record."""
    dlq_dir = Path(target_dir) / "corrupt_or_failed"
    dlq_dir.mkdir(parents=True, exist_ok=True)
    dest = dlq_dir / Path(in_path).name
    try:
        shutil.move(in_path, dest)
    except FileNotFoundError:
        pass  # already gone; still record the failure
    return {"path": str(dest), "reason": reason, "dlq": True}



@dataclass(frozen=True)
class MatrixCell:
    """A single (category, tool, format) combination in a batch matrix."""
    category: str
    tool: str
    target_format: str

def plan_matrix(categories: List[str], tools: List[Tool | str], formats: List[str]) -> List[MatrixCell]:
    """Generate all (category, tool, format) combinations as MatrixCell instances.

    Args:
        categories: List of category strings.
        tools: List of Tool enums or tool name strings.
        formats: List of target format strings.

    Returns:
        List of MatrixCell tuples.
    """
    cells = []
    for c in categories:
        for t_member in tools:
            t_name = t_member.value if hasattr(t_member, 'value') else str(t_member)
            for f in formats:
                cells.append(MatrixCell(c, t_name, f))
    return cells

def suffix_for(cell: MatrixCell, *, multi_category: bool) -> str:
    """Generate filename suffix for a matrix cell.

    Includes category prefix only when multiple categories are present in the batch.

    Args:
        cell: MatrixCell instance.
        multi_category: Whether to include category in suffix.

    Returns:
        String suffix (e.g., "_magick" or "_general_magick").
    """
    suffix = f"_{cell.tool}"
    if multi_category:
        suffix = f"_{cell.category}{suffix}"
    return suffix

def output_name(stem: str, cell: MatrixCell, *, multi_category: bool) -> str:
    """Generate output filename for a converted image.

    Args:
        stem: Source filename stem (without extension).
        cell: MatrixCell instance.
        multi_category: Whether to include category in suffix.

    Returns:
        Full output filename with extension.
    """
    return f"{stem}{suffix_for(cell, multi_category=multi_category)}.{cell.target_format}"


class DirectoryScanner:
    """Helper for scanning input directories for supported images."""
    @staticmethod
    def scan(source_dir: str, input_files: Optional[List[str]] = None) -> List[str]:
        source_path = Path(source_dir)
        if not source_path.exists():
            raise ValueError(f"Source directory {source_dir} does not exist.")
        
        valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".heic", ".heif", ".avif"}
        
        input_paths = []
        if input_files is not None:
            for p in input_files:
                path_obj = Path(p)
                if path_obj.is_file() and path_obj.suffix.lower() in valid_exts:
                    input_paths.append(str(path_obj))
        else:
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                input_paths = [
                    str(p) for p in source_path.iterdir() 
                    if p.is_file() and p.suffix.lower() in valid_exts
                ]
                if input_paths:
                    break
                if attempt < max_attempts:
                    log.info(f"Empty scan for source_dir {source_dir}, retrying in 0.5s (attempt {attempt}/{max_attempts})...")
                    time.sleep(0.5)
        return input_paths


class MetricsCollector:
    """Helper for collecting resource and savings metrics on completion."""
    @staticmethod
    def collect(
        input_paths: List[str],
        executed_cells: List[MatrixCell],
        total_bytes_written: int,
        all_telemetry_summaries: List[Dict[str, Any]],
        duration_ms: float
    ) -> Dict[str, Any]:
        from ..core.telemetry import aggregate_telemetry
        telemetry = aggregate_telemetry(all_telemetry_summaries) if all_telemetry_summaries else {}
        
        per_image_input_bytes = 0
        for p in input_paths:
            try:
                per_image_input_bytes += os.path.getsize(p)
            except OSError:
                pass
        input_bytes = per_image_input_bytes * len(executed_cells)
        output_bytes = total_bytes_written

        duration_s = max(duration_ms / 1000.0, 1e-3)
        yield_mb_sec = (output_bytes / (1024 * 1024)) / duration_s
        savings_pct = (1.0 - output_bytes / input_bytes) * 100.0 if input_bytes else 0.0

        return {
            "cpu_avg": telemetry.get("cpu_avg", 0.0),
            "cpu_peak": telemetry.get("cpu_peak", 0.0),
            "ram_peak": telemetry.get("ram_peak", 0.0),
            "yield_mb_sec": yield_mb_sec,
            "savings_pct": savings_pct
        }


class BatchOrchestrator:
    """Coordinates batch conversion across multiple tools and format combinations.

    Manages converter instances, interpolates per-image quality via heuristics,
    monitors resources, and writes batch summary metrics on completion.
    """
    def __init__(self):
        """Initialize orchestrator with converter instances and heuristic interpolator."""
        self.repo = BatchRepository()
        self.run_controls: RunControlRegistry = {}
        self.progress: dict[int, dict] = {}
        self.interpolator = HeuristicInterpolator(HEURISTIC_TABLE_PATH)

        # Register HEIF/AVIF support for Pillow metadata probing
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
            log.info("Registered HEIF/AVIF support for Pillow (pillow-heif)")
        except ImportError:
            log.warning("pillow-heif not installed. Probing HEIC/AVIF files may fail.")

        # Resolve local binary paths for self-contained Windows execution
        ffmpeg_bin = str(PROJ_ROOT / "bin" / "ffmpeg" / "ffmpeg.exe")
        if not os.path.exists(ffmpeg_bin):
            # Try deeper path if the top-level doesn't exist (handle zip extracts)
            alt_ffmpeg = str(PROJ_ROOT / "bin" / "ffmpeg" / "8.1.1-essentials_build" / "ffmpeg.exe")
            ffmpeg_bin = alt_ffmpeg if os.path.exists(alt_ffmpeg) else "ffmpeg"

        magick_bin = str(PROJ_ROOT / "bin" / "magick" / "magick.exe")
        if not os.path.exists(magick_bin):
            magick_bin = "magick"

        self.converters = {
            "magick": MagickConverter(magick_path=magick_bin),
            "ffmpeg": FFmpegConverter(ffmpeg_path=ffmpeg_bin),
            "vips":   VipsConverter(),
            "sharp":  SharpConverter(port=8765),
        }
    def _preflight_resources(self, target_dir: str) -> None:
        """Validate available memory and disk space before batch execution.

        Delegates to the shared image_guards module (single source of truth for
        the preflight thresholds shared with the calibration runner).

        Raises:
            ValueError: If insufficient memory or disk space.
        """
        from .image_guards import preflight_resources
        preflight_resources(target_dir)

    def _check_free_disk(self, target_dir: str) -> None:
        """Check available disk space during batch execution.

        Delegates to the shared image_guards module.

        Raises:
            ValueError: If disk space is critically low.
        """
        from .image_guards import check_free_disk
        check_free_disk(target_dir)

    def _probe_quality(self, path: str, category: str, tool: str, target_format: str, cached_dim: tuple[int, int] | None = None) -> float:
        """Probes a single image and returns its target quality."""
        try:
            if cached_dim is not None:
                w, h = cached_dim
            else:
                from ..core.utils import probe_image_dimensions
                w, h = probe_image_dimensions(path)
            return self.interpolator.get_interpolated_quality(
                category, target_format, tool, w, h
            )
        except Exception as e:
            log.error(f"Failed to read metadata for {path}: {e}")
            return default_quality_for(tool, target_format)

    def _probe_all_dimensions(self, paths: list[str]) -> Dict[str, tuple[int, int]]:
        """Probe dimensions of all input images in parallel.

        Returns (0, 0) for any file that cannot be probed instead of propagating
        the exception — callers treat (0, 0) as unreadable and reject the file.
        """
        from ..core.utils import probe_image_dimensions
        from concurrent.futures import ThreadPoolExecutor

        def _safe_probe(path: str) -> tuple[int, int]:
            try:
                return probe_image_dimensions(path)
            except Exception as e:
                log.warning("Could not probe dimensions for %s: %s", Path(path).name, e)
                return (0, 0)

        workers = min(32, (os.cpu_count() or 4) * 4)
        from ..core.tracing import bind_context
        with ThreadPoolExecutor(max_workers=workers) as ex:
            dims = list(ex.map(bind_context(_safe_probe), paths))
        return dict(zip(paths, dims))

    def execute_batch(self, run_id: int, request: BatchRequest) -> None:
        """Execute a batch job across multiple (category, tool, format) combinations.

        Scans source directory, probes image dimensions and heuristic quality for each
        (category, tool, format) cell, invokes the appropriate converter, aggregates
        metrics, and writes batch_summary to database on completion.

        Args:
            run_id: Unique batch identifier (references batch_runs row).
            request: BatchRequest with source_dir, target_dir, formats, tools, categories.
        """
        start_time = time.time()
        ctrl = self.run_controls.setdefault(run_id, RunControl())
        
        # State variables shared across execution and finalization phases
        all_success_count = 0
        all_failure_count = 0
        total_bytes_written = 0
        all_errors = []
        all_telemetry_summaries = []
        analytics_records: List[dict] = []
        input_paths = []
        executed_cells: List[MatrixCell] = []
        total_conversions = 0
        cancelled = False
        failed_during_run = False
        failure_reason = None

        try:
            # 1. Scan source_dir using DirectoryScanner
            input_paths = DirectoryScanner.scan(request.source_dir, request.input_files)
            
            # Pre-flight resource validation check
            self._preflight_resources(request.target_dir)
            
            if not input_paths:
                if request.input_files is not None:
                    err_msg = f"No images found in {request.source_dir} after filtering specific files."
                else:
                    err_msg = f"No images found in {request.source_dir} after 3 scan attempts — check the path is reachable and contains supported files."
                raise ValueError(err_msg)

            # Per-batch circuit-breaker isolation (issue 49x): converters are
            # long-lived singletons shared across batches. Reset every breaker at
            # the start of each run so poison-pill files from a prior batch cannot
            # bleed into this one and quarantine healthy files during the cooldown.
            # Guarded: a converter without the BaseConverter breaker (e.g. a test
            # stub) simply has no state to reset.
            for converter in self.converters.values():
                set_run = getattr(converter, "_set_active_run_id", None)
                if callable(set_run):
                    set_run(run_id)
                reset = getattr(converter, "_reset_failures", None)
                if callable(reset):
                    reset()

            # Matrix Configuration
            categories = request.category if isinstance(request.category, list) else [request.category]
            tools = request.tool if isinstance(request.tool, list) else [request.tool]
            formats = request.target_format if isinstance(request.target_format, list) else [request.target_format]
            
            plan = plan_matrix(categories, tools, formats)
            multi_category = len(categories) > 1
            total_conversions = len(input_paths) * len(plan)
            
            self.progress[run_id] = {
                "cells_done": 0,
                "cells_total": len(plan),
                "current_cell": None,
                "ok": 0,
                "fail": 0,
                "started_at": start_time,
            }
            
            log.info(f"Starting Matrix Batch: {len(input_paths)} images * {len(plan)} cells = {total_conversions} conversions")
            
            dim_cache = self._probe_all_dimensions(input_paths)

            # Filter unreadable and massive images upfront via the shared guard
            # (single source of truth, also used by the calibration runner).
            from .image_guards import partition_images
            input_paths, rejected = partition_images(input_paths, dim_cache)
            for rej in rejected:
                log.error(rej["error"])
                all_failure_count += len(plan)
                for _ in plan:
                    all_errors.append(rej)

            from concurrent.futures import ThreadPoolExecutor
            probe_workers = min(32, (os.cpu_count() or 4) * 4)

            cells_processed = 0
            abort_matrix = False

            for cell in plan:
                if abort_matrix:
                    break
                ctrl.wait_if_paused()
                if ctrl.cancelled:
                    cancelled = True
                    break
                
                self.progress[run_id]["current_cell"] = f"{cell.category}/{cell.tool}/{cell.target_format}"
                
                t_name = cell.tool
                cat = cell.category
                fmt = cell.target_format

                converter = self.converters.get(t_name)
                if converter:
                    set_run = getattr(converter, "_set_active_run_id", None)
                    if callable(set_run):
                        set_run(run_id)
                if not converter:
                    # Skip an unregistered tool's cell so sibling cells still run
                    # (partial success). If NO cell executes, the post-loop guard
                    # fails the whole batch.
                    err_msg = f"Unsupported tool: {t_name}"
                    log.error(err_msg)
                    all_failure_count += len(input_paths)
                    all_errors.append({"path": "N/A", "error": err_msg})
                    continue
                
                if converter.is_broken:
                    err_msg = f"Quarantined: {t_name} circuit breaker tripped — not attempted."
                    log.error(f"Aborting sub-batch: {t_name} is marked as BROKEN. Quarantining {len(input_paths)} files.")
                    all_failure_count += len(input_paths)
                    for _p in input_paths:
                        all_errors.append({"path": _p, "error": err_msg, "quarantined": True})
                    continue

                if DISK_RECHECK_EVERY_CELLS > 0 and cells_processed > 0 and cells_processed % DISK_RECHECK_EVERY_CELLS == 0:
                    try:
                        self._check_free_disk(request.target_dir)
                    except ValueError as e:
                        log.error(f"Mid-run check failed: {e}")
                        abort_matrix = True
                        break

                log.info(f"Processing Matrix Cell: [{cat}] [{t_name}] [{fmt}]")
                
                # Probe qualities for this combination
                from ..core.tracing import bind_context
                with ThreadPoolExecutor(max_workers=probe_workers) as ex:
                    qualities = list(ex.map(bind_context(lambda p: self._probe_quality(p, cat, t_name, fmt, dim_cache.get(p))), input_paths))

                suffix = suffix_for(cell, multi_category=multi_category)

                result = converter.convert_batch(
                    input_paths,
                    request.target_dir,
                    fmt,
                    qualities,
                    run_id=run_id,
                    suffix=suffix,
                    dimensions=dim_cache
                )
                if isinstance(result, dict):
                    from app.core.converters.base import BatchResult
                    tool_val = result.get("tool")
                    result = BatchResult(
                        success_count=result.get("success_count", 0),
                        failure_count=result.get("failure_count", 0),
                        duration_ms=result.get("duration_ms", 0.0),
                        telemetry=result.get("telemetry", {}),
                        errors=result.get("errors", []),
                        bytes_written=result.get("bytes_written", 0),
                    )
                    if tool_val:
                        result.tool = tool_val
                
                # Quarantine failed files to DLQ
                quarantined_errors = []
                for err in result.errors:
                    if err.get("path"):
                        rec = quarantine_to_dlq(err["path"], request.target_dir, reason=err.get("error", "conversion failed"))
                        quarantined_errors.append({
                            "path": rec["path"],
                            "error": rec["reason"],
                            "dlq": True
                        })
                        log.warning("file quarantined to DLQ", extra={"subprocess": {"path": rec["path"], "reason": rec["reason"]}})
                    else:
                        quarantined_errors.append(err)

                all_success_count += result.success_count
                all_failure_count += result.failure_count
                total_bytes_written += result.bytes_written
                all_errors.extend(quarantined_errors)
                if result.telemetry:
                    all_telemetry_summaries.append(result.telemetry)
                    
                cells_processed += 1
                progress_dict = self.progress[run_id]
                progress_dict["cells_done"] = cells_processed
                progress_dict["ok"] = all_success_count
                progress_dict["fail"] = all_failure_count
                
                executed_cells.append(cell)

                # Per-conversion analytics for the heuristic feedback loop. A path
                # succeeded if it is not among this cell's error paths.
                actual_tool = getattr(result, "tool", None) or t_name
                error_paths = {e.get("path") for e in result.errors if e.get("path")}
                for img_path, q in zip(input_paths, qualities):
                    analytics_records.append({
                        "path": img_path, "category": cat, "format": fmt, "tool": actual_tool,
                        "quality": q, "success": img_path not in error_paths,
                    })

            # Nothing ran at all (every tool unregistered, all images unreadable)
            # while failures accrued → the batch failed; raise so the except
            # handler marks it 'failed'. Exception: a fully-quarantined batch
            # (broken converter) is a handled outcome — fall through so its
            # per-file quarantine errors are still persisted via save_errors.
            if not executed_cells and all_failure_count > 0:
                if not any(e.get("quarantined") for e in all_errors):
                    raise ValueError("Batch produced no executed cells (all tools unsupported or all images unreadable).")

        except Exception as e:
            log.error(f"Batch execution failed for run {run_id}: {e}")
            failed_during_run = True
            failure_reason = str(e)

        # 4. Finalize stage (OUTSIDE the execution try/except!)
        # Any error during metrics collection or summary logging must be handled
        # gracefully without marking a successful batch as failed.
        try:
            if cancelled:
                def _mark_cancelled():
                    with get_connection() as conn:
                        self.repo.update_status(conn, run_id, "cancelled",
                                                total_images=total_conversions)
                with_db_retry(_mark_cancelled, max_retries=SQLITE_BUSY_ATTEMPTS,
                               initial_delay=SQLITE_BUSY_BASE_DELAY_S)
                if all_failure_count > 0:
                    try:
                        with get_connection() as conn:
                            self.repo.save_errors(conn, run_id, all_errors)
                    except Exception as err:
                        log.warning(f"save_errors dropped {len(all_errors)} rows: {err}")
                return

            if failed_during_run:
                # Mark as failed if we failed during scanning, preflight or converter execution
                def _fail():
                    with get_connection() as conn:
                        self.repo.update_status(conn, run_id, "failed")
                with_db_retry(_fail, max_retries=SQLITE_BUSY_ATTEMPTS, initial_delay=SQLITE_BUSY_BASE_DELAY_S)
                
                if all_failure_count > 0 or failure_reason:
                    errs_to_save = all_errors if all_errors else [{"path": None, "error": failure_reason}]
                    try:
                        with get_connection() as conn:
                            self.repo.save_errors(conn, run_id, errs_to_save)
                    except Exception as err:
                        log.warning(f"save_errors dropped {len(errs_to_save)} rows: {err}")
                return

            # Compute and save metrics summary using MetricsCollector
            duration_ms = (time.time() - start_time) * 1000
            metrics = MetricsCollector.collect(
                input_paths=input_paths,
                executed_cells=executed_cells,
                total_bytes_written=total_bytes_written,
                all_telemetry_summaries=all_telemetry_summaries,
                duration_ms=duration_ms
            )

            # A batch that produced zero successful conversions while accruing
            # failures (e.g. every tool unregistered, all images unreadable) is a
            # failure, not a silent "completed". Partial success stays "completed".
            final_status = "failed" if all_success_count == 0 and all_failure_count > 0 else "completed"

            def _save_summary():
                with get_connection() as conn:
                    self.repo.save_summary(
                        conn,
                        batch_id=run_id,
                        duration_ms=duration_ms,
                        cpu_avg_pct=metrics.get("cpu_avg", 0.0),
                        cpu_peak_pct=metrics.get("cpu_peak", 0.0),
                        ram_peak_mb=metrics.get("ram_peak", 0.0),
                        yield_mb_sec=metrics.get("yield_mb_sec", 0.0),
                        savings_pct=metrics.get("savings_pct", 0.0),
                        success_count=all_success_count,
                        failure_count=all_failure_count,
                    )
                    self.repo.update_status(conn, run_id, final_status, total_images=total_conversions)
            
            with_db_retry(_save_summary, max_retries=SQLITE_BUSY_ATTEMPTS, initial_delay=SQLITE_BUSY_BASE_DELAY_S)

            _emit_job_metrics(
                final_status=final_status,
                executed_cells_tools=[c.tool for c in executed_cells],
                formats=[c.target_format for c in executed_cells],
                duration_s=duration_ms / 1000.0,
                savings_pct=metrics.get("savings_pct", 0.0),
            )

            if all_failure_count > 0:
                try:
                    with get_connection() as conn:
                        self.repo.save_errors(conn, run_id, all_errors)
                except Exception as err:
                    log.warning(f"save_errors dropped {len(all_errors)} rows: {err}")

            # Best-effort: persist per-conversion analytics so the heuristic
            # generators can learn from real batch runs. Never fails the batch.
            if analytics_records:
                try:
                    from ..core.db.repositories.conversions import record_conversions
                    with get_connection() as conn:
                        record_conversions(conn, analytics_records)
                except Exception as err:
                    log.warning(f"Analytics recording failed (best-effort): {err}")

        except Exception as e:
            log.error(f"Error during finalization for run {run_id}: {e}")
        finally:
            self.run_controls.pop(run_id, None)
            self.progress.pop(run_id, None)


def _emit_job_metrics(final_status, executed_cells_tools, formats, duration_s, savings_pct):
    """Record Prometheus counters for a finished batch (no-op when metrics off)."""
    try:
        from .metrics import record_job, observe_processing_seconds, observe_compression_ratio
        observe_processing_seconds(duration_s)
        # compression_ratio = output/input = (1 - savings/100)
        observe_compression_ratio(max(0.0, 1.0 - (savings_pct / 100.0)))
        for tool in set(executed_cells_tools):
            for fmt in set(formats):
                record_job(status=final_status, tool=tool, fmt=fmt)
    except Exception:
        pass

