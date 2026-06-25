# Serial SSIM Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline `calibrate` CLI command that serially finds the per-image encoder quality hitting a target SSIM (scored in-process with OpenCV), writes the measured qualities to the analytics DB, and regenerates `heuristic_table.json`.

**Architecture:** Three pure `app/core` modules — `similarity.py` (cv2 SSIM over pyvips-decoded pixels), `calibrator.py` (serial secant/binary quality search), and a config helper (`quality_direction_for`) — plus one orchestration module in `app/batch_api/calibration_runner.py` that reuses `BatchOrchestrator`'s converters + interpolator, persists results, and auto-chains `generate_heuristic_table`. A new `app/cli.py` subcommand drives it. Normal batches are untouched.

**Tech Stack:** Python, OpenCV (`cv2`), `pyvips` (`.numpy()`), NumPy, SQLite, argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-06-25-serial-ssim-calibration-design.md`

---

## File Structure

New:
- `app/core/similarity.py` — decode + cv2 SSIM scoring. Pure, no DB, no subprocess.
- `app/core/calibrator.py` — `find_optimal_quality` serial search. Pure, depends only on `config` + `similarity`.
- `app/batch_api/calibration_runner.py` — offline run orchestration. Reuses `BatchOrchestrator`, repositories, `generate_heuristic_table`. (Lives in `batch_api`, not `core`, so the lower `core` layer never imports the higher `batch_api` layer, and stored tool names match the live path: `magick/ffmpeg/vips/sharp`.)
- `tests/core/test_similarity.py`
- `tests/core/test_calibrator.py`
- `tests/batch_api/test_calibration_runner.py` (integration-marked)

Modified:
- `app/core/config.py` — add `QUALITY_DIRECTION_BY_TOOL_FORMAT` + `quality_direction_for`.
- `app/cli.py` — add `calibrate` subcommand.

---

## Task 1: Config search-direction helper

**Files:**
- Modify: `app/core/config.py` (insert after `quality_range_for`, around line 210)
- Test: `tests/core/test_config_direction.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_config_direction.py
from app.core import config

def test_direction_default_is_ascending():
    assert config.quality_direction_for("vips", "webp") == "ascending"
    assert config.quality_direction_for("sharp", "jxl") == "ascending"
    assert config.quality_direction_for("magick", "avif") == "ascending"

def test_direction_ffmpeg_avif_is_descending():
    assert config.quality_direction_for("ffmpeg", "avif") == "descending"

def test_direction_is_case_insensitive_on_format():
    assert config.quality_direction_for("ffmpeg", "AVIF") == "descending"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_config_direction.py -v`
Expected: FAIL with `AttributeError: module 'app.core.config' has no attribute 'quality_direction_for'`

- [ ] **Step 3: Write minimal implementation**

In `app/core/config.py`, immediately after the `quality_range_for` function (after line 210):

```python
# Search direction per (tool, format): "descending" means a LOWER native value
# is better quality (ffmpeg avif is a libaom CRF, 0..63, lower = better). All
# other paths are 0..100 "higher is better". Used by the calibration search.
QUALITY_DIRECTION_BY_TOOL_FORMAT: dict[tuple[str, str], str] = {
    ("ffmpeg", "avif"): "descending",
}


def quality_direction_for(tool: str, target_format: str) -> str:
    """Resolve the search direction for (tool, format).

    Returns "descending" when a lower native quality value yields better
    quality, else "ascending".
    """
    fmt = target_format.lower()
    return QUALITY_DIRECTION_BY_TOOL_FORMAT.get((tool, fmt), "ascending")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_config_direction.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add app/core/config.py tests/core/test_config_direction.py
git commit -m "feat(config): add quality_direction_for for calibration search"
```

---

## Task 2: SSIM scoring module

**Files:**
- Create: `app/core/similarity.py`
- Test: `tests/core/test_similarity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_similarity.py
import numpy as np
from app.core import similarity


def test_compute_ssim_identical_is_one():
    a = np.random.default_rng(0).integers(0, 256, (64, 64, 3)).astype(np.uint8)
    assert similarity.compute_ssim(a, a) > 0.999


def test_compute_ssim_degraded_is_lower():
    a = np.full((64, 64, 3), 128, np.uint8)
    b = a.copy()
    b[::2] = 0
    assert similarity.compute_ssim(a, b) < similarity.compute_ssim(a, a)


def test_score_ssim_decode_failure_returns_sentinel(monkeypatch):
    def boom(_path):
        raise RuntimeError("undecodable")
    monkeypatch.setattr(similarity, "decode_rgb", boom)
    assert similarity.score_ssim("orig.png", "cand.webp") == -1.0


def test_score_ssim_shape_mismatch_returns_sentinel(monkeypatch):
    monkeypatch.setattr(similarity, "decode_rgb", lambda _p: np.zeros((10, 10, 3), np.uint8))
    orig = np.zeros((20, 20, 3), np.uint8)
    assert similarity.score_ssim("o", "c", orig_rgb=orig) == -1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_similarity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.similarity'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/similarity.py
"""In-process SSIM scoring for calibration.

Decodes images to RGB via pyvips (no temp files) and computes the reference
Wang et al. SSIM with OpenCV. Standard SSIM scale, so config.TARGET_SSIM stays
meaningful. No native binary, no subprocess.
"""

import cv2
import numpy as np
import pyvips

from .logger import get_logger

log = get_logger(__name__)

# SSIM stabilisation constants for 8-bit data (L = 255).
_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2
_WIN = (11, 11)
_SIGMA = 1.5


def decode_rgb(path: str) -> np.ndarray:
    """Decode any supported image to a contiguous H*W*3 uint8 array via pyvips."""
    arr = pyvips.Image.new_from_file(path).numpy()
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    return np.ascontiguousarray(arr[:, :, :3])


def compute_ssim(a: np.ndarray, b: np.ndarray) -> float:
    """Mean Wang SSIM over two H*W*3 uint8 arrays (11x11 Gaussian, sigma 1.5)."""
    a = a.astype(np.float32)
    b = b.astype(np.float32)
    mu1 = cv2.GaussianBlur(a, _WIN, _SIGMA)
    mu2 = cv2.GaussianBlur(b, _WIN, _SIGMA)
    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2
    sigma1_sq = cv2.GaussianBlur(a * a, _WIN, _SIGMA) - mu1_sq
    sigma2_sq = cv2.GaussianBlur(b * b, _WIN, _SIGMA) - mu2_sq
    sigma12 = cv2.GaussianBlur(a * b, _WIN, _SIGMA) - mu1_mu2
    ssim_map = ((2 * mu1_mu2 + _C1) * (2 * sigma12 + _C2)) / (
        (mu1_sq + mu2_sq + _C1) * (sigma1_sq + sigma2_sq + _C2)
    )
    return float(ssim_map.mean())


def score_ssim(orig_path: str, conv_path: str, *, orig_rgb: np.ndarray = None) -> float:
    """Decode original (or reuse orig_rgb) and candidate, return SSIM.

    Returns -1.0 on any failure (decode error, dimension mismatch) so the
    calibration search treats that quality point as failed.
    """
    try:
        original = orig_rgb if orig_rgb is not None else decode_rgb(orig_path)
        candidate = decode_rgb(conv_path)
        if original.shape != candidate.shape:
            log.warning(
                "SSIM shape mismatch %s vs %s for %s", original.shape, candidate.shape, conv_path
            )
            return -1.0
        return compute_ssim(original, candidate)
    except Exception as e:
        log.warning("SSIM scoring failed for %s: %s", conv_path, e)
        return -1.0
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_similarity.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/core/similarity.py tests/core/test_similarity.py
git commit -m "feat(core): add in-process cv2 SSIM scoring (similarity.py)"
```

---

## Task 3: Serial quality search (calibrator)

**Files:**
- Create: `app/core/calibrator.py`
- Test: `tests/core/test_calibrator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/test_calibrator.py
from pathlib import Path

from app.core import calibrator


class FakeConverter:
    """Records the quality used per output path; writes a size-proportional file."""

    def __init__(self, name):
        self._name = name
        self.q_by_path = {}

    def get_name(self):
        return self._name

    def convert(self, inp, out, fmt, quality, is_intermediate=False, run_id=None):
        size = max(1, int(round(float(quality))) + 1)
        Path(out).write_bytes(b"x" * size)
        self.q_by_path[out] = float(quality)
        return {
            "success": True,
            "fatal_error": False,
            "duration_ms": 1.0,
            "bytes_written": size,
        }


def _scorer(conv, model):
    def score(_orig, conv_path, *, orig_rgb=None):
        return model(conv.q_by_path[conv_path])
    return score


def test_ascending_converges_to_target(tmp_path):
    conv = FakeConverter("vips")
    model = lambda q: min(1.0, 0.90 + 0.001 * q)  # q=80 -> 0.98
    res = calibrator.find_optimal_quality(
        conv, "in.png", "webp", "vips", str(tmp_path),
        target_ssim=0.98, initial_quality=50, score_fn=_scorer(conv, model),
    )
    assert res.get("quality_found") is not None
    assert res["ssim_achieved"] >= 0.98 - 1e-9
    assert 75 <= res["quality_found"] <= 92


def test_descending_crf_converges_to_target(tmp_path):
    conv = FakeConverter("ffmpeg")
    model = lambda crf: min(1.0, 1.0 - 0.005 * crf)  # crf=4 -> 0.98
    res = calibrator.find_optimal_quality(
        conv, "in.png", "avif", "ffmpeg", str(tmp_path),
        target_ssim=0.98, initial_quality=20, score_fn=_scorer(conv, model),
    )
    assert res.get("quality_found") is not None
    assert res["ssim_achieved"] >= 0.98 - 1e-9
    assert res["quality_found"] <= 10


def test_unreachable_target_returns_best_effort_capped(tmp_path):
    conv = FakeConverter("vips")
    res = calibrator.find_optimal_quality(
        conv, "in.png", "webp", "vips", str(tmp_path),
        target_ssim=0.99, max_iters=6, score_fn=_scorer(conv, lambda q: 0.5),
    )
    assert res.get("quality_found") is not None
    assert res["iterations"] <= 6


def test_all_points_fail_returns_error(tmp_path):
    conv = FakeConverter("vips")
    res = calibrator.find_optimal_quality(
        conv, "in.png", "webp", "vips", str(tmp_path),
        score_fn=lambda *a, **k: -1.0,
    )
    assert "error" in res
    assert res.get("quality_found") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/core/test_calibrator.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.calibrator'`

- [ ] **Step 3: Write minimal implementation**

```python
# app/core/calibrator.py
"""Serial SSIM-targeted quality search.

Ports the main app's predictive secant + binary-fallback search, stripped of
GPU, MS-SSIM, stop-events and process pools. Pure: depends only on config and
similarity. Scores via an injectable score_fn (defaults to similarity.score_ssim)
so the loop is testable without native encoders.
"""

import os
from pathlib import Path

from .config import (
    TARGET_SSIM,
    MAX_CALIBRATION_ITERS,
    CALIBRATION_SSIM_TOLERANCE,
    quality_range_for,
    quality_direction_for,
)
from .logger import get_logger

log = get_logger(__name__)


def _next_quality_guess(initial_quality, history_q, history_ssim, target_ssim, low, high, use_float):
    """Predictive secant step from the last two points; bisection fallback.

    Returns the next quality to try, or None when the search is stuck.
    """
    if not history_q and initial_quality is not None:
        mid = float(initial_quality)
    elif len(history_q) >= 2:
        q1, q2 = history_q[-2], history_q[-1]
        s1, s2 = history_ssim[-2], history_ssim[-1]
        if abs(s2 - s1) < 1e-6:
            mid = (low + high) / 2.0
        else:
            mid = q2 + (target_ssim - s2) * (q2 - q1) / (s2 - s1)
    else:
        mid = (low + high) / 2.0

    mid = max(low, min(high, mid))
    mid_to_apply = round(mid, 2) if use_float else int(round(mid))

    if history_q and mid_to_apply == history_q[-1]:
        mid = (low + high) / 2.0
        mid_to_apply = round(mid, 2) if use_float else int(round(mid))
        if mid_to_apply == history_q[-1]:
            return None
    return mid_to_apply


def find_optimal_quality(
    converter,
    input_path,
    target_format,
    tool,
    output_dir,
    *,
    target_ssim=TARGET_SSIM,
    max_iters=MAX_CALIBRATION_ITERS,
    initial_quality=None,
    tolerance=CALIBRATION_SSIM_TOLERANCE,
    orig_rgb=None,
    score_fn=None,
):
    """Find the smallest-output quality whose SSIM meets target_ssim.

    Returns a dict {quality_found, ssim_achieved, iterations, history, best_path,
    output_size_bytes, duration_ms} on success, or {"error": ...} on failure.
    """
    if score_fn is None:
        from .similarity import score_ssim as score_fn

    qr = quality_range_for(tool, target_format)
    low, high = float(qr[0]), float(qr[1])
    ascending = quality_direction_for(tool, target_format) == "ascending"
    use_float = isinstance(qr[0], float) or isinstance(qr[1], float)

    history_q = []
    history_ssim = []
    best_quality = None
    best_ssim = -1.0
    best_path = None
    best_size = float("inf")
    best_duration = 0.0
    last_error = "no successful quality point"

    os.makedirs(output_dir, exist_ok=True)
    stem = Path(input_path).stem

    iterations = 0
    while iterations < max_iters:
        iterations += 1
        q = _next_quality_guess(
            initial_quality if iterations == 1 else None,
            history_q, history_ssim, target_ssim, low, high, use_float,
        )
        if q is None:
            break

        out_path = os.path.join(output_dir, f"{stem}_{tool}_{iterations}.{target_format}")
        result = converter.convert(input_path, out_path, target_format, q, is_intermediate=True)

        if not result.get("success"):
            last_error = result.get("error", "converter error")
            if result.get("fatal_error"):
                log.error("Calibration aborted (fatal) for %s via %s", input_path, tool)
                break
            # Shrink the range away from the failing end and retry.
            if ascending:
                low = q + (0.1 if use_float else 1)
            else:
                high = q - (0.1 if use_float else 1)
            continue

        current_ssim = score_fn(input_path, out_path, orig_rgb=orig_rgb)
        if current_ssim == -1.0:
            last_error = "similarity scoring failed"
            if ascending:
                low = q + (0.1 if use_float else 1)
            else:
                high = q - (0.1 if use_float else 1)
            continue

        current_size = result.get("bytes_written") or os.path.getsize(out_path)
        history_q.append(q)
        history_ssim.append(current_ssim)

        # Best tracking: prefer the smallest output that meets target; if nothing
        # meets target yet, keep the highest-SSIM attempt as a best-effort.
        if current_ssim >= target_ssim:
            if current_size < best_size:
                best_quality, best_ssim, best_path, best_size, best_duration = (
                    q, current_ssim, out_path, current_size, result.get("duration_ms", 0.0)
                )
        elif best_size == float("inf") and current_ssim > best_ssim:
            best_quality, best_ssim, best_path, best_duration = (
                q, current_ssim, out_path, result.get("duration_ms", 0.0)
            )

        # Binary bounds (safety net around the secant step).
        if ascending:
            if current_ssim < target_ssim:
                low = q
            else:
                high = q
        else:
            if current_ssim < target_ssim:
                high = q
            else:
                low = q

        if abs(current_ssim - target_ssim) <= tolerance:
            break
        if len(history_ssim) >= 3:
            d1 = abs(history_ssim[-1] - history_ssim[-2])
            d2 = abs(history_ssim[-2] - history_ssim[-3])
            if d1 < 0.0001 and d2 < 0.0001:
                break

    if best_quality is None:
        return {"error": f"Calibration failed: {last_error}"}

    if best_size == float("inf"):
        try:
            best_size = os.path.getsize(best_path)
        except OSError:
            best_size = 0

    return {
        "quality_found": best_quality,
        "ssim_achieved": best_ssim,
        "iterations": iterations,
        "history": list(zip(history_q, history_ssim)),
        "best_path": best_path,
        "output_size_bytes": int(best_size),
        "duration_ms": best_duration,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/core/test_calibrator.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/core/calibrator.py tests/core/test_calibrator.py
git commit -m "feat(core): add serial SSIM-targeted quality search (calibrator.py)"
```

---

## Task 4: Calibration run orchestrator

**Files:**
- Create: `app/batch_api/calibration_runner.py`
- Test: `tests/batch_api/test_calibration_runner.py` (integration-marked, added in Task 6)

- [ ] **Step 1: Write the implementation**

```python
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
from .orchestrator import BatchOrchestrator, plan_matrix

log = get_logger(__name__)

VALID_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".heic", ".heif", ".avif"}


def run_calibration(
    source_dir,
    categories,
    tools,
    formats,
    *,
    sample=30,
    target_ssim=TARGET_SSIM,
    regenerate_table=True,
):
    """Run serial calibration over a capped sample and regenerate priors.

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

    orch = BatchOrchestrator()
    repo = BatchRepository()
    plan = plan_matrix(categories, tools, formats)

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

    usable = [i for i in images if i in orig_cache]
    tmp_dir = src / "_calibration_tmp"
    tmp_dir.mkdir(exist_ok=True)

    calibrated = 0
    failures = 0
    try:
        for cell in plan:
            converter = orch.converters.get(cell.tool)
            if converter is None:
                log.error("Unknown tool '%s'; skipping cell.", cell.tool)
                continue

            for img in usable[:sample]:
                w, h = dims.get(img, (0, 0))
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
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        with get_connection() as conn:
            repo.update_status(conn, run_id, "completed", total_images=calibrated)

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
```

- [ ] **Step 2: Sanity-import check**

Run: `python -c "from app.batch_api.calibration_runner import run_calibration; print('ok')"`
Expected: prints `ok` (no ImportError; confirms no circular import with `orchestrator`).

- [ ] **Step 3: Commit**

```bash
git add app/batch_api/calibration_runner.py
git commit -m "feat(batch_api): add offline serial calibration runner"
```

---

## Task 5: `calibrate` CLI subcommand

**Files:**
- Modify: `app/cli.py` (subparser block around line 113; dispatch around line 116; new handler near `_run_convert`)

- [ ] **Step 1: Add the subparser**

In `app/cli.py`, after the `tui`/`doctor` parser registrations (after line 114 `sub.add_parser("doctor", ...)`), add:

```python
    p_cal = sub.add_parser("calibrate", help="Serial SSIM calibration; regenerates the heuristic table.")
    p_cal.add_argument("--source", "-s", required=True, help="Directory of sample images.")
    p_cal.add_argument("--tools", default="magick,ffmpeg,vips,sharp", help="Comma-separated tools.")
    p_cal.add_argument("--formats", default="webp,avif,jxl", help="Comma-separated target formats.")
    p_cal.add_argument("--categories", default="general", help="Comma-separated categories.")
    p_cal.add_argument("--sample", type=int, default=30, help="Max images per matrix cell.")
    p_cal.add_argument("--target-ssim", type=float, default=0.98, help="Target SSIM.")
    p_cal.add_argument("--no-regen", action="store_true", help="Skip heuristic table regeneration.")
```

- [ ] **Step 2: Add the dispatch branch**

In `main()`, after the `doctor` branch (after line 126 `_run_doctor()`), add:

```python
    elif args.command == "calibrate":
        _run_calibrate(args)
```

- [ ] **Step 3: Add the handler**

After `_run_convert(...)` (after line 175), add:

```python
def _run_calibrate(args) -> None:
    # Enable the calibration write gate live (config reads this attribute at call
    # time; setting the module attribute is robust regardless of import order).
    os.environ["PIXELPIVOT_CALIBRATION_ENABLED"] = "true"
    from app.core import config
    config.CALIBRATION_ENABLED = True

    from app.batch_api.calibration_runner import run_calibration

    def _split(s):
        return [x.strip() for x in s.split(",") if x.strip()]

    summary = run_calibration(
        args.source,
        _split(args.categories),
        _split(args.tools),
        _split(args.formats),
        sample=args.sample,
        target_ssim=args.target_ssim,
        regenerate_table=not args.no_regen,
    )
    print(
        f"Calibration run {summary['run_id']}: {summary['calibrated']} calibrated, "
        f"{summary['failures']} failed, across {summary['cells']} cells."
    )
    if summary.get("table"):
        print(f"Heuristic table regenerated: {summary['table']['heuristic_table']}")
    else:
        print("Heuristic table not regenerated.")
```

- [ ] **Step 4: Verify the CLI wires up**

Run: `python -m app.cli calibrate --help`
Expected: usage text listing `--source`, `--tools`, `--formats`, `--categories`, `--sample`, `--target-ssim`, `--no-regen`.

- [ ] **Step 5: Commit**

```bash
git add app/cli.py
git commit -m "feat(cli): add calibrate subcommand"
```

---

## Task 6: End-to-end integration test

**Files:**
- Create: `tests/batch_api/test_calibration_runner.py`

This test exercises the real converters + cv2 + pyvips. It uses an isolated
SQLite DB via `PIXELPIVOT_DB_PATH`, lowers the heuristic sample gate, and skips
cleanly when native encoders are unavailable.

- [ ] **Step 1: Write the integration test**

```python
# tests/batch_api/test_calibration_runner.py
import importlib
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

pytestmark = pytest.mark.integration


def _make_image(path, seed):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:240, 0:320]
    base = (np.sin(xx / 20.0) + np.cos(yy / 18.0)) * 40 + 128
    arr = np.stack([base, base * 0.9 + 20, base * 0.8 + 40], -1)
    arr = (arr + rng.normal(0, 5, arr.shape)).clip(0, 255).astype(np.uint8)
    Image.fromarray(arr).save(path)


def test_run_calibration_writes_conversions_and_regenerates(tmp_path, monkeypatch):
    db_path = tmp_path / "calib.db"
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(db_path))

    # Rebind modules that captured the DB path / gate at import time.
    from app.core.db import connection as db_connection
    importlib.reload(db_connection)
    from app.core.db import schema as db_schema
    importlib.reload(db_schema)
    db_schema.init_db()

    from app.core import config
    monkeypatch.setattr(config, "CALIBRATION_ENABLED", True)
    monkeypatch.setattr(config, "HEURISTIC_MIN_SAMPLES", 1)

    src = tmp_path / "samples"
    src.mkdir()
    for i in range(2):
        _make_image(src / f"img_{i}.png", seed=i)

    table_out = tmp_path / "heuristic_table.json"

    from app.batch_api import calibration_runner

    # Skip if the chosen encoder is not available in this environment.
    orch = calibration_runner.BatchOrchestrator()
    probe = orch.converters["vips"].convert(
        str(src / "img_0.png"), str(tmp_path / "probe.webp"), "webp", 80, is_intermediate=True
    )
    if not probe.get("success"):
        pytest.skip(f"vips/webp encoder unavailable: {probe.get('error')}")

    summary = calibration_runner.run_calibration(
        str(src), ["general"], ["vips"], ["webp"],
        sample=10, target_ssim=0.95,
        regenerate_table=False,  # call generator explicitly with the low gate below
    )

    assert summary["calibrated"] >= 1

    from app.core.db import get_connection
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT quality, calib_method FROM conversions WHERE success = 1"
        ).fetchall()
        assert len(rows) >= 1
        assert all(r["calib_method"] == "ssim" for r in rows)
        cal_rows = conn.execute("SELECT COUNT(*) AS n FROM calibration_results").fetchone()
        assert cal_rows["n"] >= 1

    from app.core.heuristic import generate_heuristic_table
    with get_connection() as conn:
        result = generate_heuristic_table(
            conn=conn, table_path=table_out, weights_path=tmp_path / "w.json"
        )
    import json
    table = json.loads(Path(result["heuristic_table"]).read_text())
    assert "general" in table and "webp" in table["general"]
```

- [ ] **Step 2: Run the integration test**

Run: `pytest tests/batch_api/test_calibration_runner.py -v -m integration`
Expected: PASS, or SKIP with "vips/webp encoder unavailable" if libvips/webp is missing in the environment.

- [ ] **Step 3: Run the full unit suite (no regressions)**

Run: `pytest tests/core/test_similarity.py tests/core/test_calibrator.py tests/core/test_config_direction.py -v`
Expected: all PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/batch_api/test_calibration_runner.py
git commit -m "test: end-to-end calibration runner integration test"
```

---

## Notes for the implementer

- **No icons in test files** (project rule — Python may not render them).
- **No `Co-Authored-By` trailer** in commits (project rule).
- `pytest.ini` may need an `integration` marker registered; if `pytest -m integration` warns about an unknown marker, add `markers = integration: requires native encoders` under `[pytest]`. Check existing real-asset E2E tests first — they likely already register it.
- The converter `convert()` contract (`app/core/converters/base.py:231`) returns `success`, `fatal_error`, `duration_ms`, `bytes_written`, `error` — the calibrator relies only on these.
- `quality_range_for` returns float bounds for every (tool, format) in the batch engine, so calibration qualities are fractional (rounded to 2 dp). This matches the engine's existing fractional-quality philosophy — never cast to int.

---

## Self-Review

**Spec coverage:**
- cv2 in-process scoring + pyvips decode → Task 2.
- `quality_direction_for` (ascending default, ffmpeg/avif descending) → Task 1.
- Serial secant/binary search seeded by heuristic, fatal-error abort, sentinel handling, smallest-output-meeting-target → Task 3 + Task 4 (seed) .
- Persist to `conversions` (`calib_method="ssim"`) + `images` + `calibration_results` → Task 4.
- `batch_runs` provenance parent via `create_run(trigger_type="calibration")` → Task 4.
- Capped sample per cell → Task 4 (`usable[:sample]`).
- Auto-chain `generate_heuristic_table` → Task 4 + CLI.
- CLI `calibrate` with the calibration gate enabled live → Task 5.
- Unit tests (no binaries) + marked integration → Tasks 1-3, 6.
- Spec deviation logged: runner lives in `app/batch_api/`, not `app/core/`, to preserve layering (core must not import batch_api) and tool-name consistency. CLI sets `config.CALIBRATION_ENABLED = True` directly (not only env) so the write gate fires regardless of import order.

**Placeholder scan:** none — every code step is complete.

**Type/name consistency:** `find_optimal_quality(converter, input_path, target_format, tool, output_dir, *, ...)` signature is identical in Task 3 and its call in Task 4. Return keys (`quality_found`, `ssim_achieved`, `iterations`, `history`, `output_size_bytes`, `duration_ms`) match between Task 3 implementation and Task 4 consumer. `decode_rgb`/`compute_ssim`/`score_ssim` names match across Tasks 2, 3, 4, 6. `quality_direction_for`/`quality_range_for` usage matches Task 1.
