# Task 014 — Symmetric target_format validation across request models

**Severity:** LOW-MED (REST accepts junk formats; silent per-file failures downstream)
**Phase:** I — request parsing / validation boundary
**Confidence:** Confirmed by code read

## Problem

The two request models validate `target_format` asymmetrically:

- `app/batch_api/models.py:38` -> `BatchRequest.target_format: Annotated[List[str], ...]`
  (any string accepted).
- `app/batch_api/models.py:52` -> `HotFolderRequest.target_format:
  Annotated[List[TargetFormat], ...]` where `TargetFormat = Literal["webp","avif","jxl"]`
  (`models.py:14`).

So `POST /batch/start` with `target_format=["garbage"]` is accepted at the boundary, then
fails per-file deep in the converters (`MagickConverter.FORMAT_PARAMS.get(...)` returns
None, `magick_converter.py:80-82`) — a late, opaque failure instead of a clean 422.

## Fix

Use `TargetFormat` (the existing `Literal`) for `BatchRequest.target_format` too, so both
entry points reject unknown formats at the schema boundary. Confirm the orchestrator's
`isinstance(..., list)` handling (`orchestrator.py:158-160`) is unaffected.

## TDD plan

RED — `tests/test_task_014.py` (ASCII only):
1. `BatchRequest(source_dir=..., target_dir=..., target_format=["garbage"], tool=["magick"])`
   currently constructs successfully; assert it raises `pydantic.ValidationError` after
   the fix. Fails today.
2. Regression: `target_format=["webp","avif","jxl"]` still constructs.
3. Regression: empty list still rejected (preserves [task_004](completed/task_004_backend_matrix_validation.md)).

GREEN:
- Change the annotation to `Annotated[List[TargetFormat], Field(min_length=1)]`.

## Acceptance criteria
- Unknown formats are rejected with a 422 at the API boundary for BOTH models.
- Valid formats and the min_length=1 rule still hold.
- ASCII-only test assertions.
