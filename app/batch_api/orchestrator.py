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
from ..core.converters.cavif_converter import CavifConverter
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


def quarantine_rejected(rejected: List[dict], target_dir: str) -> List[dict]:
    """Route upfront-rejected inputs to the DLQ, returning dlq error records.

    Rejects come from the shared partition gate (partition_images), which runs
    once before any converter — so every tool sees the same usable set and
    reject counts are identical cross-tool. Moving each rejected file to
    corrupt_or_failed/ here (instead of only logging it) means malformed inputs
    land in the DLQ with a reason, consistent with per-file conversion failures.
    Pathless records (e.g. an unsupported-tool error) are passed through as-is.
    """
    out: List[dict] = []
    for rej in rejected:
        path = rej.get("path")
        reason = rej.get("error", "rejected")
        if path and path != "N/A":
            rec = quarantine_to_dlq(path, target_dir, reason=reason)
            out.append({"path": rec["path"], "error": rec["reason"], "dlq": True})
        else:
            out.append(rej)
    return out



def cross_tool_fallback_tool() -> Optional[str]:
    """Alternate tool for per-file fallback, or None when disabled (default).

    Env-gated via PIXELPIVOT_FALLBACK_TOOL. Off by default so batches stay
    deterministic; set to a tool name (e.g. "vips") to opt in.
    """
    tool = os.getenv("PIXELPIVOT_FALLBACK_TOOL", "").strip().lower()
    return tool or None


def apply_cross_tool_fallback(errors: List[dict], retry_one: Callable[[str], bool]):
    """Split a cell's error records into (recovered_paths, remaining_errors).

    ``retry_one(path)`` re-runs the file on the alternate tool and returns True
    on success. Errors without a path (e.g. an unsupported-tool marker) are kept
    as-is — there is nothing to retry.
    """
    recovered: List[str] = []
    remaining: List[dict] = []
    for err in errors:
        path = err.get("path")
        if path and retry_one(path):
            recovered.append(path)
        else:
            remaining.append(err)
    return recovered, remaining


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
        if not source_path.is_absolute():
            # Windows UNC (\\server\share) or drive paths (C:\...) look relative on Linux
            if source_dir.startswith("\\\\") or (len(source_dir) > 1 and source_dir[1] == ":"):
                raise ValueError(
                    f"Windows path '{source_dir}' is not accessible from the container. "
                    "Mount the share in WSL (e.g. sudo mount -t cifs //server/share /mnt/share), "
                    "add a volume in docker-compose.yml, then use the Linux container path."
                )
        if not source_path.exists():
            raise ValueError(f"Source directory {source_dir} does not exist.")
        
        valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".heic", ".heif", ".avif"}
        
        input_paths = []
        if input_files is not None:
            for p in input_files:
                path_obj = Path(p)
                full_path = source_path / path_obj if not path_obj.is_absolute() else path_obj
                if full_path.is_file() and full_path.suffix.lower() in valid_exts:
                    input_paths.append(str(full_path))
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
        duration_ms: float,
        input_sizes: Dict[str, int] | None = None
    ) -> Dict[str, Any]:
        from ..core.telemetry import aggregate_telemetry
        telemetry = aggregate_telemetry(all_telemetry_summaries) if all_telemetry_summaries else {}
        
        per_image_input_bytes = 0
        for p in input_paths:
            if input_sizes is not None:
                per_image_input_bytes += input_sizes.get(p, 0)
            else:
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

        # Resolve local binary paths in a data-driven way
        import sys
        is_win = sys.platform == "win32"
        ext = ".exe" if is_win else ""
        
        tool_candidates = {
            "ffmpeg": [
                Path("bin") / "ffmpeg" / f"ffmpeg{ext}",
                Path("bin") / "ffmpeg" / "8.1.1-essentials_build" / f"ffmpeg{ext}",
            ],
            "magick": [
                Path("bin") / "magick" / f"magick{ext}",
            ],
            "cavif": [
                Path("bin") / "cavif" / f"cavif{ext}",
            ],
        }
        
        resolved_bins = {}
        for tool, candidates in tool_candidates.items():
            resolved = None
            for cand in candidates:
                full_path = PROJ_ROOT / cand
                if full_path.exists():
                    resolved = str(full_path)
                    break
            if resolved is None:
                resolved = tool  # Fallback to system PATH
            resolved_bins[tool] = resolved

        ffmpeg_bin = resolved_bins["ffmpeg"]
        magick_bin = resolved_bins["magick"]
        cavif_bin = resolved_bins["cavif"]


        # Dynamically discover and instantiate all registered converters using reflection (inspect)
        import inspect
        from ..core.converters.base import get_converter_registry
        
        config = {
            "ffmpeg_path": ffmpeg_bin,
            "magick_path": magick_bin,
            "cavif_path": cavif_bin,
            "port": 8765,
        }
        
        self.converters = {}
        for name, registry_cls in get_converter_registry().items():
            # Support mock patching in tests by resolving via globals() first
            cls_name = registry_cls.__name__
            cls = globals().get(cls_name, registry_cls)
            
            # If the class has been mocked, it might not have an __init__ signature we can inspect,
            # or the mock class itself might not require configuration.
            try:
                sig = inspect.signature(cls.__init__)
                kwargs = {}
                for param in sig.parameters.values():
                    if param.name in config:
                        kwargs[param.name] = config[param.name]
            except Exception:
                kwargs = {}
                
            self.converters[name] = cls(**kwargs)
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

    def _fallback_retry_one(
        self,
        in_path: str,
        alt_tool: str,
        target_dir: str,
        target_format: str,
        quality: float,
        suffix: str,
        run_id: Optional[int],
    ) -> bool:
        """Retry a single failed file on the alternate tool. True on success.

        Writes to the same cell output name (same suffix) so a recovered file
        satisfies the cell it was failing in. Never raises — any error in the
        alternate path just means the fallback did not recover the file.
        """
        alt = self.converters.get(alt_tool)
        if alt is None or getattr(alt, "is_broken", False):
            return False
        stem = Path(in_path).stem
        out_path = str(Path(target_dir) / f"{stem}{suffix}.{target_format}")
        try:
            res = alt.convert(in_path, out_path, target_format, quality, run_id=run_id)
            return bool(getattr(res, "success", False))
        except Exception as e:
            log.warning("cross-tool fallback via %s failed for %s: %s", alt_tool, Path(in_path).name, e)
            return False

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

    def _scan_and_preflight(self, request: BatchRequest) -> tuple[List[str], Dict[str, int]]:
        """Scan source directory and run preflight resource checks."""
        input_paths = DirectoryScanner.scan(request.source_dir, request.input_files)
        if getattr(request, "sample", None) is not None:
            input_paths = input_paths[:request.sample]
        
        input_sizes = {}
        for p in input_paths:
            try:
                input_sizes[p] = os.path.getsize(p)
            except OSError:
                input_sizes[p] = 0
                
        self._preflight_resources(request.target_dir)
        
        if not input_paths:
            if request.input_files is not None:
                err_msg = f"No images found in {request.source_dir} after filtering specific files."
            else:
                err_msg = f"No images found in {request.source_dir} after 3 scan attempts — check the path is reachable and contains supported files."
            raise ValueError(err_msg)
            
        return input_paths, input_sizes

    def _reset_converters(self, run_id: int) -> None:
        """Reset circuit breakers on all converters for the start of a run."""
        for converter in self.converters.values():
            set_run = getattr(converter, "_set_active_run_id", None)
            if callable(set_run):
                set_run(run_id)
            reset = getattr(converter, "_reset_failures", None)
            if callable(reset):
                reset()

    def _prepare_image_plan(
        self, input_paths: List[str], plan: List[MatrixCell], target_dir: str
    ) -> tuple[List[str], Dict[str, tuple[int, int]], int, List[Dict]]:
        """Probe dimensions, partition unreadable/huge images, and quarantine rejects."""
        dim_cache = self._probe_all_dimensions(input_paths)
        
        from .image_guards import partition_images
        active_paths, rejected = partition_images(input_paths, dim_cache)
        
        failure_count = len(rejected) * len(plan)
        rejects_errors = []
        for rej in quarantine_rejected(rejected, target_dir):
            log.error(rej["error"])
            rejects_errors.append(rej)
            
        return active_paths, dim_cache, failure_count, rejects_errors

    def _finalize_batch_run(
        self,
        run_id: int,
        start_time: float,
        cancelled: bool,
        failed_during_run: bool,
        failure_reason: Optional[str],
        input_paths: List[str],
        executed_cells: List[MatrixCell],
        total_bytes_written: int,
        all_telemetry_summaries: List[Dict],
        input_sizes: Dict[str, int],
        all_success_count: int,
        all_failure_count: int,
        total_conversions: int,
        all_errors: List[Dict],
        analytics_records: List[Dict]
    ) -> None:
        """Perform batch run finalization (db saving, metrics logging, cleanup)."""
        try:
            if cancelled:
                def _mark_cancelled():
                    with get_connection() as conn:
                        self.repo.update_status(conn, run_id, "cancelled",
                                                total_images=total_conversions)
                with_db_retry(_mark_cancelled, max_retries=SQLITE_BUSY_ATTEMPTS,
                               initial_delay=SQLITE_BUSY_BASE_DELAY_S)()
                if all_failure_count > 0:
                    try:
                        with get_connection() as conn:
                            self.repo.save_errors(conn, run_id, all_errors)
                    except Exception as err:
                        log.warning(f"save_errors dropped {len(all_errors)} rows: {err}")
                return

            if failed_during_run:
                def _fail():
                    with get_connection() as conn:
                        self.repo.update_status(conn, run_id, "failed")
                with_db_retry(_fail, max_retries=SQLITE_BUSY_ATTEMPTS, initial_delay=SQLITE_BUSY_BASE_DELAY_S)()
                
                if all_failure_count > 0 or failure_reason:
                    errs_to_save = all_errors if all_errors else [{"path": None, "error": failure_reason}]
                    try:
                        with get_connection() as conn:
                            self.repo.save_errors(conn, run_id, errs_to_save)
                    except Exception as err:
                        log.warning(f"save_errors dropped {len(errs_to_save)} rows: {err}")
                return

            duration_ms = (time.time() - start_time) * 1000
            metrics = MetricsCollector.collect(
                 input_paths=input_paths,
                 executed_cells=executed_cells,
                 total_bytes_written=total_bytes_written,
                 all_telemetry_summaries=all_telemetry_summaries,
                 duration_ms=duration_ms,
                 input_sizes=input_sizes
             )

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
            
            with_db_retry(_save_summary, max_retries=SQLITE_BUSY_ATTEMPTS, initial_delay=SQLITE_BUSY_BASE_DELAY_S)()

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

            if analytics_records:
                try:
                    from ..core.db.repositories.conversions import record_conversions
                    with get_connection() as conn:
                        record_conversions(conn, analytics_records)
                except Exception as err:
                    log.warning(f"Analytics recording failed (best-effort): {err}")

            # Automatically generate HTML report in the target directory
            try:
                with get_connection() as conn:
                    run_info = self.repo.get_run(conn, run_id)
                if run_info and run_info.get("target_dir"):
                    from app.core.reports.generator import generate_report_for_run
                    from app.batch_api.models import _resolve_path
                    target_dir = _resolve_path(run_info["target_dir"])
                    report_path = os.path.join(target_dir, f"batch_report_run_{run_id}.html")
                    generate_report_for_run(run_id, report_path)
                    log.info(f"HTML batch report generated successfully: {report_path}")
            except Exception as report_err:
                log.warning(f"Failed to automatically generate HTML report: {report_err}")

        except Exception as e:
            log.error(f"Error during finalization for run {run_id}: {e}")
        finally:
            self.run_controls.pop(run_id, None)
            self.progress.pop(run_id, None)

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

        input_sizes: Dict[str, int] = {}

        try:
            # 1. Scan and preflight
            input_paths, input_sizes = self._scan_and_preflight(request)
            
            # 2. Reset converters
            self._reset_converters(run_id)

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
            
            # 3. Prepare image plan
            active_paths, dim_cache, fail_count, rejects_errs = self._prepare_image_plan(
                input_paths, plan, request.target_dir
            )
            all_failure_count += fail_count
            all_errors.extend(rejects_errs)

            # 4. Matrix Execution Loop
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
                    err_msg = f"Unsupported tool: {t_name}"
                    log.error(err_msg)
                    all_failure_count += len(active_paths)
                    all_errors.append({"path": "N/A", "error": err_msg})
                    cells_processed += 1
                    self.progress[run_id]["cells_done"] = cells_processed
                    self.progress[run_id]["fail"] = all_failure_count
                    continue
                
                if hasattr(converter, "supported_formats"):
                    formats_list = converter.supported_formats()
                    if isinstance(formats_list, list) and fmt not in formats_list:
                        log.info(f"Skipping unsupported combination: {t_name} does not support {fmt} encoding.")
                        cells_processed += 1
                        self.progress[run_id]["cells_done"] = cells_processed
                        continue

                if converter.is_broken:
                    err_msg = f"Quarantined: {t_name} circuit breaker tripped — not attempted."
                    log.error(f"Aborting sub-batch: {t_name} is marked as BROKEN. Quarantining {len(active_paths)} files.")
                    all_failure_count += len(active_paths)
                    for _p in active_paths:
                        all_errors.append({"path": _p, "error": err_msg, "quarantined": True})
                    cells_processed += 1
                    self.progress[run_id]["cells_done"] = cells_processed
                    self.progress[run_id]["fail"] = all_failure_count
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
                    qualities = list(ex.map(bind_context(lambda p: self._probe_quality(p, cat, t_name, fmt, dim_cache.get(p))), active_paths))

                suffix = suffix_for(cell, multi_category=multi_category)

                result = converter.convert_batch(
                    active_paths,
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
                
                fb_tool = cross_tool_fallback_tool()
                if fb_tool and fb_tool != t_name and result.errors:
                    q_by_path = dict(zip(active_paths, qualities))
                    recovered, result.errors = apply_cross_tool_fallback(
                        result.errors,
                        lambda p: self._fallback_retry_one(
                            p, fb_tool, request.target_dir, fmt,
                            q_by_path.get(p, default_quality_for(fb_tool, fmt)),
                            suffix, run_id,
                        ),
                    )
                    if recovered:
                        n = len(recovered)
                        result.success_count += n
                        result.failure_count = max(0, result.failure_count - n)
                        log.info("cross-tool fallback via %s recovered %d file(s)", fb_tool, n)

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

                actual_tool = getattr(result, "tool", None) or t_name
                error_paths = {e.get("path") for e in result.errors if e.get("path")}
                for img_path, q in zip(active_paths, qualities):
                    analytics_records.append({
                        "path": img_path, "category": cat, "format": fmt, "tool": actual_tool,
                        "quality": q, "success": img_path not in error_paths,
                    })

            if not executed_cells and all_failure_count > 0:
                if not any(e.get("quarantined") for e in all_errors):
                    raise ValueError("Batch produced no executed cells (all tools unsupported or all images unreadable).")

        except Exception as e:
            log.error(f"Batch execution failed for run {run_id}: {e}")
            failed_during_run = True
            failure_reason = str(e)

        # 5. Finalize run
        self._finalize_batch_run(
            run_id=run_id,
            start_time=start_time,
            cancelled=cancelled,
            failed_during_run=failed_during_run,
            failure_reason=failure_reason,
            input_paths=input_paths,
            executed_cells=executed_cells,
            total_bytes_written=total_bytes_written,
            all_telemetry_summaries=all_telemetry_summaries,
            input_sizes=input_sizes,
            all_success_count=all_success_count,
            all_failure_count=all_failure_count,
            total_conversions=total_conversions,
            all_errors=all_errors,
            analytics_records=analytics_records
        )


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

