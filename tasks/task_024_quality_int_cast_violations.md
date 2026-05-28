# Task 024 — Stop truncating fractional quality with int() casts in converters

**Severity:** HIGH (correctness; the entire heuristic-interpolator value
proposition is fractional `q = a + b*log10(MP)`. Casting to int silently
discards every decimal — every "perfect" quality call lands on the nearest
integer below it, biased downward.)
**Feature:** C7 + N6 (quality-scalar non-uniformity)
**Air-gap relevance:** general (the bug is the same on every host)

## Reproduction (the breadcrumb)

Static evidence — grep `app/core/converters/`:

```
app/core/converters/vips_converter.py:41:        image.webpsave(output_path, Q=int(quality))
app/core/converters/vips_converter.py:42:        params = {"method": "webpsave", "Q": int(quality)}
app/core/converters/vips_converter.py:46:        image.heifsave(output_path, compression="av1", Q=int(quality))
app/core/converters/vips_converter.py:47:        params = {"method": "heifsave", "compression": "av1", "Q": int(quality)}
app/core/converters/ffmpeg_nvenc_converter.py:39:  "avif": lambda q: ["-c:v","av1_nvenc","-cq",str(max(0,min(51,int((100-q)/2)))),...]
app/core/converters/ffmpeg_nvenc_converter.py:40:  "heic": lambda q: ["-c:v","hevc_nvenc","-cq",str(max(0,min(51,int((100-q)/2)))),...]
app/core/converters/magick_converter.py:61:       img.quality = int(quality)
```

Dynamic evidence: feed the interpolator a representative pair and observe
the fractional value before it reaches the converter. Example -- in a fresh
Python REPL on this repo:

```python
>>> from app.core.heuristic_interpolator import HeuristicInterpolator
>>> from app.core.paths import APP_ROOT
>>> hi = HeuristicInterpolator(APP_ROOT/"core"/"heuristic_table.json")
>>> hi.get_interpolated_quality("general","webp","vips",1920,1080)
80.0   # falls back to default_quality_for since the shipped table is empty
```

Once the table has data (the curve is `a + b*log10(MP)`), the value is
fractional (e.g. 87.43). `vips_converter.py:41` then calls
`image.webpsave(..., Q=int(87.43))` -> `Q=87`. Across a thousand-image
batch, this is a systematic downward quality bias of ~0.5/image vs. the
fitted curve's intent. Worse, the recorded `parameters_used.Q` is now 87,
so the analytics feedback loop (`task_008`) re-learns the curve from the
truncated samples and the bias compounds.

Constraint N6 from `codebase_inspection_prompt.md` explicitly forbids the
`int()` cast inside converter implementations.

## Root cause (from the code, not a doc)

Three converters were written before the heuristic-interpolator change
(`task_016`) made quality fractional:

- `vips_converter.py:41-47` — `Q=int(quality)` for the `webpsave` and
  `heifsave` library calls. pyvips' `Q` parameter accepts float for
  `heifsave` since libheif 1.13+; for `webpsave`, libvips accepts a float
  via the GValue conversion. Either way, dropping `int()` is correct.
- `ffmpeg_nvenc_converter.py:39-40` — `int((100-q)/2)` maps quality 0..100
  to NVENC `-cq` 0..51 with integer-only stepping. The map should accept
  the fractional `q`; round to nearest integer only at the boundary (which
  the encoder ultimately requires), and do so with `round()`, not `int()`,
  so 87.6 -> 6 (not 6 from a downcast). Better still: clamp first, then
  apply `round` to the final integer scalar that ffmpeg actually demands.
- `magick_converter.py:61` — `img.quality = int(quality)` inside the
  Wand fallback. ImageMagick's `quality` field is integer-valued so a cast
  is unavoidable at the boundary; do it with `round()` and document why.

The subprocess paths for magick (`MagickConverter.FORMAT_PARAMS` at
`magick_converter.py:32-34`) already pass `str(q)` -- ImageMagick CLI parses
fractional `-quality 87.5` correctly. Keep those untouched.

The pyvips JXL path (`vips_converter.py:48-56`) already maps via
`quality_to_jxl_distance(quality)` which preserves the float -- keep that
behavior.

## Required behavior

- pyvips `webpsave` / `heifsave` receive the float `quality` directly with
  no `int()`.
- The `parameters_used` dict logged with each conversion records the
  fractional quality, not the truncated one.
- NVENC mapping uses `round((100 - quality) / 2)` -> clamp to [1, 51] -> the
  integer is generated only at the very last step inside the lambda.
- Wand fallback in `magick_converter.py` uses `round(quality)` (still
  integer for ImageMagick's `quality` field, but unbiased).
- No `int(quality)` survives anywhere in `app/core/converters/`.

## TDD plan

RED -- `tests/test_task_024.py` (ASCII only):

1. Static grep test: walk every `.py` under `app/core/converters/` and
   assert that no source line matches the regex `int\s*\(\s*quality\b` and
   no line matches `int\(\s*\(\s*100\s*-\s*q` (catches the nvenc lambda).
   Today this fails on 5 lines.
2. Behavior test for pyvips webp: build a small fixture PNG, call
   `VipsConverter().convert(fixture, out, "webp", 87.6)`, then re-open `out`
   with pyvips and read its `Q` from the source params dict; assert the
   recorded value equals `87.6` (or matches `pytest.approx(87.6)`), not 87.
3. Behavior test for nvenc mapping (no GPU required): import the lambda
   from `FFmpegNvencConverter.FORMAT_PARAMS["avif"]`, call with `q=87.6`,
   parse the `-cq` value from the produced args; assert it equals `round((100
   - 87.6) / 2)` (i.e. 6), not 6 produced via `int((100-87.6)/2)=int(6.2)=6`
   coincidentally; pick a value where round vs int differs, e.g. q=86.6 ->
   (100-86.6)/2 = 6.7 -> round=7, int=6.
4. Behavior test for magick Wand fallback (only run if `wand` is importable;
   skip otherwise with `pytest.importorskip("wand")`): patch the subprocess
   to fail; verify the fallback recorded `quality` is `round(87.6) = 88`,
   not 87.

GREEN -- minimal change:

- `vips_converter.py:41,42,46,47`: drop `int(...)`; pass `quality` (float)
  directly. Record `Q=quality` in `params`.
- `ffmpeg_nvenc_converter.py:39,40`: change `int((100-q)/2)` to
  `round((100-q)/2)`. Add `from typing import Optional` (the file uses
  `Optional[int]` on line 61 without importing it -- saved today only by
  `from __future__ import annotations`).
- `magick_converter.py:61`: `img.quality = round(quality)`.

## Acceptance criteria

- [ ] Zero matches of `int(quality)` / `int((100-q)/2)` in
      `app/core/converters/*.py`.
- [ ] pyvips `params['Q']` recorded for a fractional input equals the
      fractional input (no truncation).
- [ ] NVENC `-cq` for q=86.6 equals 7 (round), not 6 (truncate).
- [ ] Wand fallback (if available) records `round(quality)`.
- [ ] `Optional` is imported in `ffmpeg_nvenc_converter.py`.
- [ ] Full `pytest` suite green.
- [ ] Any new tunable in `app/core/config.py`.
- [ ] `convert_batch()` return shape unchanged.
- [ ] ASCII-only test code/messages.

## Constraints for the implementer (Sonnet)

TDD only (red before green, paste failing output first). No destructive ops,
no `git push` / force / amend / `--no-verify`. Fix exactly this defect -- no
drive-by refactors. Behavior identical on Python 3.12 and 3.14.
