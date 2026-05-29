"""
Matrix smoke test against ./image_samples (mixed 500-file dataset).

Iterates {ffmpeg, magick, vips, sharp, ffmpeg_nvenc} -> AVIF, one
BatchOrchestrator run per tool. Each run is its own batch_runs row so
per-tool wall-clock and savings show up cleanly in `bd memories` / the
batch_summary table.

Aligned with the post-merge schema:
  * BatchRequest fields are LISTS (target_format / tool / category).
  * BatchOrchestrator.execute_batch is SYNCHRONOUS in the current code.
  * BatchRepository.create_run takes plain strings.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import List

from app.batch_api.models import BatchRequest, Tool
from app.batch_api.orchestrator import BatchOrchestrator
from app.core.db.connection import get_connection
from app.core.db.repositories.batch import BatchRepository
from app.core.db.schema import init_db


REPO_ROOT = Path(__file__).resolve().parent
SOURCE_DIR = REPO_ROOT / "image_samples"
TARGET_BASE = REPO_ROOT / "converted_images" / "matrix_test_avif"

CATEGORY = "general"
TARGET_FORMAT = "avif"

# Tool order picks cheap-and-reliable first so a circuit-breaker trip on
# ffmpeg_nvenc (e.g. driver missing) does not poison earlier rows.
TOOLS: List[str] = [
    Tool.magick.value,
    Tool.vips.value,
    Tool.sharp.value,
    Tool.ffmpeg.value,
    Tool.ffmpeg_nvenc.value,
]


def _count_inputs(src: Path) -> int:
    valid = {".jpg", ".jpeg", ".png", ".webp", ".tiff", ".heic", ".heif", ".avif"}
    return sum(1 for p in src.iterdir() if p.is_file() and p.suffix.lower() in valid)


def run_matrix_test() -> int:
    if not SOURCE_DIR.exists():
        print(f"[FATAL] Source directory missing: {SOURCE_DIR}", file=sys.stderr)
        return 2

    init_db()

    image_count = _count_inputs(SOURCE_DIR)
    print(f"Source: {SOURCE_DIR}  ({image_count} images)")
    print(f"Target base: {TARGET_BASE}")
    print(f"Format: {TARGET_FORMAT}    Tools: {TOOLS}")

    orchestrator = BatchOrchestrator()
    repo = BatchRepository()

    overall_ok = True
    for tool in TOOLS:
        print(f"\n>>> TOOL: {tool}")

        target_dir = TARGET_BASE / tool
        target_dir.mkdir(parents=True, exist_ok=True)

        # 1. Persist the run record first so a crash mid-run is still observable.
        with get_connection() as conn:
            run_id = repo.create_run(
                conn,
                source_dir=str(SOURCE_DIR),
                target_dir=str(target_dir),
                target_format=TARGET_FORMAT,
                tool=tool,
                trigger_type="manual_test_script",
            )

        # 2. Build the matrix-shaped request (lists per merged schema).
        try:
            request = BatchRequest(
                source_dir=str(SOURCE_DIR),
                target_dir=str(target_dir),
                target_format=[TARGET_FORMAT],
                tool=[tool],
                category=[CATEGORY],
                trigger_type="manual_test_script",
            )
        except Exception as e:
            overall_ok = False
            print(f"  [SKIP] BatchRequest validation failed for {tool}: {e}")
            continue

        # 3. Execute. Orchestrator is synchronous in the merged code.
        start = time.perf_counter()
        try:
            orchestrator.execute_batch(run_id, request)
        except Exception as e:
            overall_ok = False
            print(f"  [FAIL] {tool}: {type(e).__name__}: {e}")
            continue
        elapsed = time.perf_counter() - start

        # 4. Pull the persisted summary for the report.
        with get_connection() as conn:
            summary = repo.get_summary(conn, run_id)

        print(f"  wall_clock = {elapsed:.2f}s")
        if summary:
            ok = summary.get("success_count") or 0
            fail = summary.get("failure_count") or 0
            cpu = summary.get("cpu_avg_pct") or 0.0
            savings = summary.get("savings_pct") or 0.0
            print(f"  success={ok}  failure={fail}  cpu_avg={cpu:.1f}%  savings={savings:.1f}%")
            if fail > 0:
                overall_ok = False
        else:
            overall_ok = False
            print("  [WARN] no batch_summary row written (check logs).")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(run_matrix_test())
