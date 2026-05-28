# Task 003 — Probe image dimensions once, not once per matrix cell

**Severity:** HIGH (perf; scales with `images x cells x 2`)
**Phase:** II — "Maximum Matrix" stress
**Confidence:** Confirmed by code read

## Problem

A full matrix is `6 categories x 5 engines x 3 formats = 90 cells`. Image *dimensions*
do not change between cells — only the interpolated *quality* depends on
`(category, format, tool, w, h)`. Yet dimensions are re-read from disk on every cell,
twice:

1. **Quality probe** — `orchestrator.py:136-137`:
   ```python
   with ThreadPoolExecutor(max_workers=probe_workers) as ex:
       qualities = list(ex.map(lambda p: self._probe_quality(p, cat, t_name, fmt), input_paths))
   ```
   `_probe_quality` -> `probe_image_dimensions` (`utils.py:110-132`) spawns an **ffprobe
   subprocess per image**. Across the matrix that is `N x 90` ffprobe spawns.

2. **Resolution bucketing** — `magick_converter.py:126`:
   ```python
   res_bucket = get_resolution_bucket_from_path(path)   # -> probe_image_dimensions again
   ```
   Another `N` ffprobe spawns per cell.

Net: for `N` images, a full matrix spawns on the order of `2 * N * 90` ffprobe
processes, all reading identical, unchanging dimensions.

## Fix

Probe each input **once**, before the matrix loop, into a cache:

```python
from typing import Dict, Tuple

def _probe_all_dimensions(self, paths: list[str]) -> Dict[str, Tuple[int, int]]:
    workers = min(32, (os.cpu_count() or 4) * 4)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        dims = list(ex.map(probe_image_dimensions, paths))
    return dict(zip(paths, dims))
```

Then:
- Interpolate quality per cell **in memory** from cached `(w, h)` — no I/O:
  `self.interpolator.get_interpolated_quality(cat, fmt, tool, w, h)`.
- Pass the cached dims (or pre-computed res-buckets) into `convert_batch` so
  `MagickConverter` stops re-probing. Add an optional
  `dimensions: dict[str, tuple[int,int]] | None = None` parameter to `convert_batch`
  in `BaseConverter` and override; when provided, `get_resolution_bucket(w, h)` is used
  directly instead of `get_resolution_bucket_from_path`.

This drops ffprobe spawns from `~2 * N * cells` to `N`.

## Acceptance criteria
- ffprobe (or PIL fallback) is invoked exactly once per input file for a full matrix run.
- Interpolated qualities are identical to the current per-cell results (snapshot test on a
  small fixture set).
- `MagickConverter.convert_batch` uses passed-in dimensions when available and only falls
  back to probing when the cache is absent (preserves standalone behavior).
