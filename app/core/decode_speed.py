"""
core/decode_speed.py — Option B: Synthetic image decode speed measurement.

Measures how long Python (Pillow) takes to fully decode a compressed image
from disk. No browser required. ~100× faster than Chrome LCP.

Captures:
  - Format decoder CPU performance (AVIF, JXL, WebP, JPEG all differ)
  - File size impact on read time

Does NOT capture:
  - Browser GPU decode paths
  - Compositor / layout time
  - Any network effects

Public API
----------
DecodeSpeedMeter.measure_batch(tasks, output_root, workers=8)
    Parallel decode across a thread pool.
    Returns list of (conversion_id, decode_ms | None).

DecodeSpeedMeter.measure_one(filepath)
    Single file decode. Returns decode_ms or None.
"""

import io
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing
from pathlib import Path

from PIL import Image
from .utils import get_pyvips, vips_has_loader
from .logger import get_logger

log = get_logger(__name__)


class DecodeSpeedMeter:
    def measure_one(self, filepath: str) -> float | None:
        """
        Read the file into memory, then time a full decode.
        Reading into a BytesIO buffer first isolates pure decode time
        from disk I/O variance.

        Uses Pillow (PIL) for standard formats and pyvips for modern
        formats (JXL, AVIF) that Pillow lacks native support for.
        """
        try:
            with open(filepath, "rb") as f:
                raw = f.read()

            suffix = Path(filepath).suffix.lower()
            t0 = time.perf_counter()

            # 1. Use pyvips for modern formats (JXL, AVIF) if available
            use_vips = False
            vips = get_pyvips()
            if vips:
                if suffix == ".jxl" and vips_has_loader("jxlload"):
                    use_vips = True
                elif suffix == ".avif" and vips_has_loader("heifload"):
                    use_vips = True

            if use_vips:
                # .new_from_buffer creates a VipsSourceCustom wrapper around the memory
                vimg = vips.Image.new_from_buffer(raw, "")
                # .avg() forces a full decode in memory to calculate the average pixel value
                vimg.avg()
            else:
                # 2. Use Pillow (PIL) for everything else
                buf = io.BytesIO(raw)
                img = Image.open(buf)
                img.load()  # forces full decode of every pixel
                img.close()

            decode_ms = (time.perf_counter() - t0) * 1000
            return decode_ms

        except Exception as e:
            log.warning(f"Decode failed for {filepath} ({suffix}): {e}")
            return None

    def measure_batch(
        self,
        tasks: list,
        output_root: str,
        workers: int = 8,
    ) -> list[tuple[int, float | None]]:
        """
        Measure decode speed for all tasks using a thread pool.

        `tasks` must be rows from db.get_pending_metric_tasks(), each with
        keys: id, filename, tool, format.

        Returns a list of (conversion_id, decode_ms | None) in task order.
        """
        if not tasks:
            return []

        root = Path(output_root)

        def _one(task) -> tuple[int, float | None]:
            stem = Path(task["filename"]).stem
            filename = f"{stem}_{task['tool']}_final.{task['format']}"
            filepath = root / filename

            if not filepath.exists():
                log.warning(f"File not found, skipping decode: {filepath}")
                return (task["id"], None)

            decode_ms = self.measure_one(str(filepath))
            status = f"{decode_ms:.1f}ms" if decode_ms is not None else "failed"
            log.info(
                f"  Decode {task['filename']} | {task['format']} via {task['tool']}: {status}"
            )
            return (task["id"], decode_ms)

        # Preserve input order by indexing futures
        results: list[tuple[int, float | None]] = [None] * len(tasks)

        ctx = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            future_to_idx = {pool.submit(_one, task): i for i, task in enumerate(tasks)}
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    log.error(f"Decode task {idx} raised: {e}")
                    results[idx] = (tasks[idx]["id"], None)

        return results
