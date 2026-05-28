# Task 001 — Fix `Tool` enum stringification in output suffix & logs

**Severity:** HIGH (corrupts every matrix output filename)
**Phase:** I (steel-thread) / II (boundary)
**Confidence:** Confirmed by runtime probe

## Problem

`orchestrator.py:101` sets `tools = request.tool`, which is `List[Tool]` (Pydantic
coerces the JSON strings into `Tool` enum members). The loop variable `t_name` is
therefore a `Tool` enum, not a `str`. At `orchestrator.py:140`:

```python
suffix = f"_{t_name}"
```

Because `Tool` is a `(str, Enum)` mixin, its f-string / `__str__` renders the
*member repr*, not the value:

```text
f"_{Tool.magick}"  -> '_Tool.magick'   # observed, Python 3.14.4 (local) and 3.12 (Docker)
str(Tool.magick)   -> 'Tool.magick'
Tool.magick.value  -> 'magick'         # what we actually want
```

### Impact
- Every matrix output is written as `photo_Tool.magick.webp` instead of `photo_magick.webp`.
- The MagickConverter rename step (`magick_converter.py:185-192`) faithfully applies the
  broken suffix, so the corrupted name reaches disk.
- The end-of-batch byte-counting rescan (`orchestrator.py:177-189`) rebuilds the suffix
  the same broken way, so it *happens* to find the files — masking the bug in
  `savings_pct` but not on disk.
- Log lines `orchestrator.py:133` `[{cat}] [{t_name}] [{fmt}]` print `Tool.magick`.

The converter *lookup* (`self.converters.get(t_name)`, line 117) still works because
`hash(Tool.magick) == hash("magick")`, which is why the bug is silent (files are produced,
just mis-named).

## Fix

Normalize tool members to their string value at the top of the loop. Single source of truth:

```python
for t_member in tools:
    t_name = t_member.value if isinstance(t_member, Tool) else str(t_member)
    converter = self.converters.get(t_name)
    ...
    suffix = f"_{t_name}"
    if len(categories) > 1:
        suffix = f"_{cat}{suffix}"
```

Apply the same `.value` normalization to the byte-counting rescan (or better, eliminate
the rescan per [task_005](task_005_extract_matrix_planner.md) by summing sizes from
converter results).

## Acceptance criteria
- A matrix run with `tool=[magick, ffmpeg]` produces `*_magick.<fmt>` and `*_ffmpeg.<fmt>`
  (no `Tool.` prefix).
- Log lines show `[magick]`, not `[Tool.magick]`.
- A regression test asserts `f"_{...}"`-derived suffixes equal `_<tool-value>` for every
  `Tool` member. (ASCII only — no icons in test output.)
- `savings_pct` still resolves output files after the rename.
