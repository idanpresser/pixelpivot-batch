# Task 000 — Multi-Select Matrix Audit: Summary & Architecture Verdict

**Scope:** GUI-to-Backend bridge for the new Multi-Select Matrix orchestration
(`run_panel.py -> api_client.py -> routes.py -> orchestrator.py -> MagickConverter`).
**Method:** static trace + non-destructive in-memory probes (no disk-fill, no process kills).
**Date:** 2026-05-25

---

## 1. Steel-thread verdict (Phase I)

The happy path is **structurally sound**. `List[str]` payloads serialize and
deserialize correctly end to end:

| Hop | File | Behavior |
|---|---|---|
| GUI collects multiselects | `run_panel.py:43-47` | three `st.multiselect` -> Python lists |
| Client serializes | `api_client.py:16-24` | lists -> JSON arrays over httpx POST |
| API deserializes | `routes.py:14-37` | Pydantic coerces `tool` strings -> `Tool` enum |
| Persist run row | `routes.py:23-31` | lists flattened to CSV columns |
| Execute | `orchestrator.py:115-157` | triple-nested loop `category x tool x format` |

Pydantic enum coercion works; `dict.get(Tool.magick)` resolves against the
string-keyed `converters` dict because `Tool(str, Enum)` hashes equal its value.

## 2. Architecture answer: is the matrix in the right place?

**Yes — the matrix expansion already lives in the backend** (`orchestrator.execute_batch`).
The GUI does **no** expansion; it forwards lists. Both entry points (REST route and
`hot_folder.py`) converge on the same `execute_batch`. **Do not move expansion to the
frontend** — that would duplicate logic and couple the GUI to converter internals.

The split is correct; the **shape** is not. `execute_batch` is a ~160-line god-method
mixing five responsibilities (scan, preflight, matrix iteration, telemetry aggregation,
summary persistence + savings math). SRP/OCP improvements are tracked in
[task_005](task_005_extract_matrix_planner.md). Verdict: **right place, wrong shape.**

### OCP note
The converter registry is hard-coded in `BatchOrchestrator.__init__` (`orchestrator.py:38-44`).
The matrix loop itself is OCP-clean (pure `dict.get(tool)`, no per-tool branching), but
*adding* a converter requires editing `__init__`. Low severity; optional registry refactor
folded into task_005.

## 3. Confirmed defects (with evidence)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| [001](task_001_fix_tool_enum_suffix.md) | **HIGH** | Output filenames contain `_Tool.magick` instead of `_magick` | probe: `f"_{Tool.magick}"` -> `'_Tool.magick'` |
| [002](task_002_sqlite_busy_retry.md) | **HIGH** | SQLITE_BUSY at completion loses the summary and marks a successful run "failed" | `orchestrator.py:194-221`, no retry around must-succeed writes |
| [003](task_003_probe_once_dimension_cache.md) | **HIGH** | Dimensions re-probed `N x cells x 2` times via `ffprobe` subprocesses | `orchestrator.py:136-137` + `magick_converter.py:126` |
| [004](task_004_backend_matrix_validation.md) | MED | Empty matrix accepted by API (silent zero-work "success") | probe: `BatchRequest(target_format=[], tool=[], category=[])` accepted |
| [005](task_005_extract_matrix_planner.md) | MED | `execute_batch` god-method; duplicated suffix logic | `orchestrator.py:58-221`, suffix built at lines 140 and 181 |
| [006](task_006_centralize_resource_thresholds.md) | MED | Magic 50MB thresholds inline; preflight runs once; mkdir errors swallowed | `orchestrator.py:71-85` |
| [007](task_007_path_containment_hardening.md) | LOW-MED | `_resolve_path` normalizes but does not contain; `../` escapes; `""` -> CWD | probe: `_resolve_path('../../etc/passwd')` -> `F:\DEV\etc\passwd` |

## 4. Soap-opera resilience (Phase III), verified by reading

- **Jealous Process** (kill magick mid-flight): circuit breaker exists
  (`base.py:41-49`, threshold 3) and the orchestrator honors `is_broken`
  (`orchestrator.py:125`). Event loop is not blocked (background-task thread /
  `run_in_executor`). Gap: the native `mogrify` path bypasses `_run_subprocess`
  accounting — see task_001 acceptance notes. **PASS with caveat.**
- **DB Heartbreak** (SQLITE_BUSY): only the 5s `busy_timeout` protects writes;
  no app-level retry. Telemetry summary is lost on contention. **FAIL -> task_002.**
- **Disk Divorce** (disk fills mid-run): preflight exists but runs once before the
  loop, thresholds are inline magic numbers, and `mkdir` failures are swallowed.
  **PARTIAL -> task_006.**

## 5. Recommended order
001 -> 002 -> 003 (correctness + reliability + perf), then 004/006 (hardening),
then 005 (structural refactor), then 007 (security posture).
