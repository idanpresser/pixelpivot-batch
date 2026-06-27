# app/batch_api/calibration_runner.py
"""Offline serial calibration run.

Reuses BatchOrchestrator's converters + heuristic interpolator, runs the serial
SSIM search per (image, cell), persists measured qualities to the analytics DB,
and (optionally) regenerates heuristic_table.json. Lives in batch_api so the
lower core layer never imports it, and so stored tool names match the live path.
"""

import shutil
from pathlib import Path

from ..core.logger import get_logger
from ..core.config import TARGET_SSIM
from ..core.db import get_connection
from ..core.db.repositories.batch import BatchRepository
from ..core.db.repositories.images import register_image
from ..core.db.repositories.conversions import insert_conversion
from ..core.calibrator import find_optimal_quality
from ..core.similarity import decode_rgb
from ..core.utils import probe_image_dimensions
from ..core.heuristic import generate_heuristic_table
from ..core.constraints import is_resolution_supported
from .orchestrator import BatchOrchestrator, plan_matrix
from .image_guards import preflight_resources, partition_images

log = get_logger(__name__)

VALID_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".heic", ".heif", ".avif"}


class _CalibrationCancelled(Exception):
    """Raised internally when a run_control signals cancellation mid-run."""


def run_calibration(
    source_dir,
    categories,
    tools,
    formats,
    *,
    sample=30,
    target_ssim=TARGET_SSIM,
    regenerate_table=True,
    run_id=None,
    run_control=None,
):
    """Run serial calibration over a capped sample and regenerate priors.

    When run_id is None a new batch_runs row is created (CLI path); when
    provided the caller's row is reused (API path). An optional run_control
    (duck-typed .cancelled) lets the API path cancel the run mid-flight.

    Returns a summary dict: {run_id, calibrated, failures, cells, table}.
    """
    src = Path(source_dir)
    if not src.is_dir():
        raise ValueError(f"Source directory {source_dir} does not exist.")

    images = [
        str(p) for p in src.iterdir()
        if p.is_file() and p.suffix.lower() in VALID_EXTS
    ]
    if not images:
        raise ValueError(f"No supported images found in {source_dir}.")

    # Same RAM/disk preflight the live batch path enforces. Calibration writes
    # its temp encodes into the source dir, so guard that volume.
    preflight_resources(str(src))

    orch = BatchOrchestrator()
    repo = BatchRepository()
    plan = plan_matrix(categories, tools, formats)

    if run_id is None:
        with get_connection() as conn:
            run_id = repo.create_run(
                conn, str(source_dir), str(source_dir),
                ",".join(formats), ",".join(tools), trigger_type="calibration",
            )

    # Decode + dimension-probe each image once; share across that image's cells.
    orig_cache = {}
    dims = {}
    for img in images:
        try:
            orig_cache[img] = decode_rgb(img)
            dims[img] = probe_image_dimensions(img)
        except Exception as e:
            log.warning("Skipping unreadable image %s: %s", Path(img).name, e)

    calibrated = 0
    failures = 0

    # Single-source reject of unreadable + massive images (shared with the
    # orchestrator). Each rejection counts against every matrix cell, as a batch.
    usable, rejected = partition_images([i for i in images if i in orig_cache], dims)
    for rej in rejected:
        log.warning("Rejected %s: %s", Path(rej["path"]).name, rej["error"])
        failures += len(plan)

    tmp_dir = src / "_calibration_tmp"
    tmp_dir.mkdir(exist_ok=True)

    cancelled = False
    try:
        for cell in plan:
            converter = orch.converters.get(cell.tool)
            if converter is None:
                log.error("Unknown tool '%s'; skipping cell.", cell.tool)
                continue

            for img in usable[:sample]:
                w, h = dims.get(img, (0, 0))

                if run_control is not None and run_control.cancelled:
                    log.info("Calibration cancelled at run_id=%s", run_id)
                    raise _CalibrationCancelled()

                supported, why = is_resolution_supported(cell.tool, cell.target_format, w, h)
                if not supported:
                    failures += 1
                    log.warning(
                        "Skipping %s %s/%s: %s",
                        Path(img).name, cell.tool, cell.target_format, why,
                    )
                    continue

                try:
                    initial_q = orch.interpolator.get_interpolated_quality(
                        cell.category, cell.target_format, cell.tool, w, h
                    )
                except Exception:
                    initial_q = None

                calib = find_optimal_quality(
                    converter, img, cell.target_format, cell.tool, str(tmp_dir),
                    target_ssim=target_ssim, initial_quality=initial_q,
                    orig_rgb=orig_cache[img],
                )

                if calib.get("quality_found") is None:
                    failures += 1
                    log.warning(
                        "Calibration failed for %s %s/%s: %s",
                        Path(img).name, cell.tool, cell.target_format, calib.get("error"),
                    )
                    continue

                history = [{"quality": q, "ssim": s} for q, s in calib.get("history", [])]
                with get_connection() as conn:
                    image_id = register_image(conn, img, cell.category)
                    insert_conversion(conn, {
                        "image_id": image_id,
                        "format": cell.target_format,
                        "tool": cell.tool,
                        "quality": calib["quality_found"],
                        "duration_ms": calib.get("duration_ms", 0.0),
                        "output_size_bytes": calib.get("output_size_bytes", 0),
                        "calib_ssim": calib["ssim_achieved"],
                        "calib_method": "ssim",
                        "success": True,
                     })
                    repo.save_calibration_result(
                        conn, run_id, img, target_ssim,
                        calib["quality_found"], calib["iterations"], history,
                    )
                calibrated += 1
    except _CalibrationCancelled:
        cancelled = True
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        with get_connection() as conn:
            repo.update_status(conn, run_id, "cancelled" if cancelled else "completed",
                               total_images=calibrated)

    table = None
    if regenerate_table and calibrated > 0:
        table = generate_heuristic_table()

    return {
        "run_id": run_id,
        "calibrated": calibrated,
        "failures": failures,
        "cells": len(plan),
        "table": table,
    }
