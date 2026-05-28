# Task 005 — Extract matrix orchestration from the `execute_batch` god-method

**Severity:** MEDIUM (maintainability; SRP/OCP; DRY)
**Phase:** I — SOLID audit / architecture verdict
**Confidence:** Code read

## Problem

`BatchOrchestrator.execute_batch` (`orchestrator.py:58-221`, ~160 lines) mixes five
responsibilities:

1. source scan + valid-extension filtering (`87-97`)
2. preflight resource checks (`71-85`) — see [task_006](task_006_centralize_resource_thresholds.md)
3. matrix expansion + iteration (`100-157`)
4. per-cell quality probing (`136-137`) — see [task_003](task_003_probe_once_dimension_cache.md)
5. telemetry aggregation + savings math + summary persistence (`159-216`)

Symptoms:
- **DRY violation:** the output suffix is built twice with the same fragile logic
  (`orchestrator.py:140` and again `181`); they must stay in lockstep or `savings_pct`
  silently breaks.
- **Hard to test:** matrix expansion cannot be unit-tested without a real scan + DB.
- **OCP:** the converter registry is constructed inline in `__init__`
  (`orchestrator.py:38-44`); adding a converter requires editing the constructor.

## Architecture decision (answers "should the matrix move?")

The matrix **belongs in the backend and already lives there** — keep it server-side.
The refactor is about *shape*, not *location*. Introduce two small, pure-ish units:

```python
@dataclass(frozen=True)
class MatrixCell:
    category: str
    tool: str            # already .value-normalized (see task_001)
    target_format: str

def plan_matrix(categories: list[str], tools: list[str], formats: list[str]) -> list[MatrixCell]:
    return [MatrixCell(c, t, f) for c in categories for t in tools for f in formats]

def output_name(stem: str, cell: MatrixCell, *, multi_category: bool) -> str:
    suffix = f"_{cell.category}_{cell.tool}" if multi_category else f"_{cell.tool}"
    return f"{stem}{suffix}.{cell.target_format}"
```

- `plan_matrix` is trivially unit-testable (no I/O).
- `output_name` is the **single** source of truth for naming, used by both the converter
  rename step and any size accounting (eliminating the duplicate at line 181).
- `execute_batch` shrinks to: scan -> preflight -> probe-once -> `for cell in plan_matrix(...)`
  -> aggregate -> persist.

Optional OCP improvement: replace the inline dict with a registry
(`register_converter(name)` decorator or entry-point lookup) so new converters self-register.
Low priority; only do it if a converter is added.

## Acceptance criteria
- `plan_matrix` and `output_name` are pure functions with direct unit tests (ASCII only).
- Output naming is defined in exactly one place; the byte-size accounting reuses it.
- `execute_batch` no longer rebuilds suffixes inline.
- Behavior is unchanged for existing matrix runs (snapshot of produced filenames matches,
  after [task_001](task_001_fix_tool_enum_suffix.md) is applied).
