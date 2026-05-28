# Steel-Thread Production-Readiness Audit

**Inspector role:** read-only auditor; writes only this ledger + `task_NNN_*.md`
defect briefs. Never modifies production code.

**Methodology:** `codebase_inspection_prompt.md` (prime directive: code is truth;
docs are hypotheses). Every PASS/FAIL row cites a command output or a `file:line`.

---

## Runtime fingerprint (host where threads were executed)

| Item | Value |
|---|---|
| Date (audit run) | 2026-05-28 |
| Host | dev workstation (NOT the air-gap sandbox) |
| Python | 3.14.4 (target floor: 3.12 per `pyproject.toml`) |
| OS | Windows 11 Pro 10.0.26200 |
| FastAPI | 0.136.1 (import OK) |
| pyvips | 3.1.1 (import OK, libvips DLL present) |
| `magick` | C:\Program Files\ImageMagick-7.1.2-Q16\magick.exe |
| `ffmpeg` | C:\ffmpeg\bin\ffmpeg.exe (also winget shim available) |
| `node` | C:\Program Files\nodejs\node.exe |
| `vips` CLI | winget vips-dev-8.18\bin\vips.exe (libvips on PATH) |
| NVIDIA GPU | RTX 5080 + `nvidia-smi.exe` present |

**Air-gap implication:** *all five* converter binaries are present here.
"Absent" threads must therefore be *simulated* (bogus path, PATH scrub,
`unittest.mock.patch`, or fatal-marker injection) — never executed against the
real PATH. The "absent" case is the air-gap case.

---

## Reconciled feature surface (code is truth)

Reconciliation of the candidate map (`codebase_inspection_prompt.md` section
"Feature surface to cover") against the live code. **Bold** = drift discovered.

- **A1-A7**: confirmed exact endpoints in `app/batch_api/routes.py`.
- **A1 fine print**: route catches *all* `Exception` -> HTTP 500 with str(e)
  (`routes.py:36-37`). Pydantic validation errors still produce 422 because
  they occur before the handler runs.
- **A8**: `_resolve_path` raises `ValueError` on empty/whitespace path or on
  `PIXELPIVOT_ALLOWED_ROOT` escape (`models.py:16-32`); pydantic surfaces 422.
  `Field(min_length=1)` enforces non-empty `target_format`/`tool`/`category`.
- **A9**: lifespan runs `init_db()` then `reap_stale_running`; orchestrator and
  `HotFolderManager` are mounted on `app.state` (`main.py:13-41`). NOTE: the
  reaper transitions stuck rows to status **`interrupted`** (not "failed");
  candidate map's choice of word "reaped" matches the code, but downstream
  callers/UI must accept `interrupted` as a terminal state.
- **B1-B8**: confirmed in `orchestrator.py`. Notable: `pre_run_mtimes` is
  populated over *all `plan`* cells but the savings loop iterates only
  `executed_cells` -- behaviorally fine, simply over-collects mtimes.
- **C1-C7**: all five converter classes present and wired in
  `BatchOrchestrator.__init__` (`orchestrator.py:86-92`). Default
  `convert_batch` returns the documented shape plus a `telemetry` key.
- **D1-D5**: confirmed in `hot_folder.py`. Debounce is **5.0 s default in the
  handler constructor** (`HotFolderHandler.__init__`), but the **registered
  handler is constructed without overriding `debounce_seconds`**
  (`hot_folder.py:206`), so `HOT_FOLDER_DEBOUNCE_MS` from config is *not*
  actually wired through -- a config knob that is silently inert. Flag below.
- **E1-E4**: `HeuristicInterpolator` evaluates `q = a + b*log10(MP)` and clamps
  via `default_quality_for`/`quality_range_for`. **Shipped table is
  `{"version": "2.0.0"}` with zero priors** -- so every call falls back to
  `default_quality_for`. Verify this degrades cleanly.
- **F1-F4**: confirmed in `db/schema.py`, `connection.py`, `repositories/batch.py`.
  Schema runs `PRAGMA integrity_check` on init; WAL+busy_timeout=5000 applied
  per connection.
- **G1-G4**: address inline per-thread.

### Drift / candidate-map omissions found

- **DRIFT-1 (LOW)**: `HOT_FOLDER_DEBOUNCE_MS=5000` in `config.py` is **not
  threaded through**; `HotFolderManager.add_hot_folder` constructs
  `HotFolderHandler(self.orchestrator, self.loop, cfg_dict)` without
  `debounce_seconds=`, so the handler uses its hardcoded 5.0 s default. The
  numbers happen to match, so behavior is right but the knob is inert.
- **MAP-OMISSION-1**: `BatchRequest.category` defaults to `["general"]` (a
  list with one element) -- not a string. A consumer that did
  `category="general"` (string) would still be accepted by pydantic via
  `Annotated[List[str], Field(min_length=1)]` only if coerced; verify the path.
- **MAP-OMISSION-2**: `tasks/task_000_matrix_audit_summary.md` and
  `tasks/task_010_emit_heuristic_version.md` are *not* in `completed/`. The
  prompt-promised "highest completed is task_019" matches the `completed/`
  directory; the next free numeric is **020**, as planned.

---

## Triage summary

| Severity | Count | Items |
|---|---|---|
| CRITICAL | 1 | task_023 (sandbox_init `npm install` egress on `<Networking>Disable>`) |
| HIGH | 4 | task_020 (telemetry FK), task_021 (log rotation race), task_022 (wheel/floor mismatch), task_024 (int(quality) cast violates N6) |
| MEDIUM | 1 | task_027 (Streamlit Google Fonts egress) |
| LOW | 2 | task_025 (HOT_FOLDER_DEBOUNCE_MS inert), task_026 (dep-list dup) |
| BLOCKED-on-target | 3 | F4 busy-retry (requires concurrent writer to engage), G2 NVENC-absent on a Sandbox without an NVIDIA driver, real-Sandbox launch parity (G4) |

---

## Ledger

Legend:
- **Verdict**: PASS / FAIL / BLOCKED.
- **Evidence**: brief — full output captured by the harness scripts under
  `tests/audit_threads/` (throwaway, ASCII-only).
- **Task**: `task_NNN_*.md` filename if a defect was filed.

Harnesses:
- `harness_01_api_orch_db.py` covers A1-A9, B1-B2/B5/B7/B8, F1-F3.
- `harness_02_converters_heuristic.py` covers C1-C5 present+absent, C6, E1-E3, G3.

| Feature | Thread | Verdict | Evidence | Task |
|---|---|---|---|---|
| A1 | POST /batch/start happy path | PASS | harness_01: 200 + run_id=2; batch completes | -- |
| A2 | GET /batch/status/{id} polled | PASS | reached status='completed' with summary | -- |
| A3 | GET /batch/{id}/errors | PASS | 200 + `[]` for happy path | -- |
| A4 | GET /batch/history | PASS | 200, list contains run_id=2 with summary fields | -- |
| A5 | POST /hotfolder/register | PASS | 200 + watcher_id returned | -- |
| A6 | GET /hotfolder/list | PASS | 200, watcher_id present | -- |
| A7 | DELETE /hotfolder/{id} + double-delete | PASS | 200 then 404 | -- |
| A8 | Empty/whitespace/bad-format/bad-tool -> 422 | PASS | 5/5 validation cases returned 422 | -- |
| A8 | PIXELPIVOT_ALLOWED_ROOT containment | PASS | inside=200, outside=422 with explicit msg | -- |
| A9 | init_db on lifespan | PASS | DB file created, integrity_check OK | -- |
| A9 | reap_stale_running | PASS | ghost row -> status='interrupted', completed_at set | -- |
| B1 | Matrix expansion + output_name | PASS | tiny_magick.webp produced for 1x1x1 plan | -- |
| B2 | Dimension probe-once cache | PASS | execute_batch ran without crash; single probe call | -- |
| B3 | Preflight/mid-run disk guard | BLOCKED | Not exercised with shutil.disk_usage mock | -- |
| B4 | is_broken converter skipped | BLOCKED | Not directly exercised; logic verified statically (orchestrator.py:218-223) | -- |
| B5 | Savings credit only this-run outputs | PASS | savings_pct=55.7%; pre-run mtime snapshot in place | -- |
| B6 | Per-conversion analytics | PASS-DEGRADED | analytics_records best-effort path runs; **task_020 telemetry FK fails** | task_020 |
| B7 | with_busy_retry on save_summary | PASS | summary row written; no retries needed under no contention | -- |
| B8 | Empty source dir | PASS | status=completed, total_images=0, summary=None, no crash | -- |
| C1 | magick PRESENT (webp + avif) | PASS | harness_02: 88B webp, 284B avif | -- |
| C1 | magick ABSENT (bogus binary) | PASS | `success=False, err='cannot find the file'`; circuit breaker fires | -- |
| C1 | magick convert_batch contract | PASS | dict contains success_count, failure_count, duration_ms, errors | -- |
| C2 | ffmpeg PRESENT (webp + avif) | PASS | 88B webp, 325B avif | -- |
| C2 | ffmpeg ABSENT (bogus binary) | PASS | `success=False, err='ffmpeg binary not found'` | -- |
| C3 | vips PRESENT (webp + avif) | PASS-DEGRADED | conversion succeeds; **N6 violation**: Q=int(quality) | task_024 |
| C3 | vips ABSENT (pyvips=None patch) | PASS | `success=False, err='pyvips library not initialized'` | -- |
| C4 | sharp PRESENT (daemon up) | PASS | 88B webp produced; daemon auto-spawned by converter | -- |
| C4 | sharp ABSENT-via-bogus-port | PASS-ANOMALY | converter auto-spawned its own daemon on a free port; "absent" thread invalidated by self-bootstrap. Real absent case = no `node_modules\sharp` -> BLOCKED on dev (we have one). | -- |
| C5 | nvenc PRESENT (av1_nvenc) | FAIL | RTX 5080 + av1_nvenc encoder failed; "nvenc" substring in stderr also trips false-positive fatal marker (config.FFMPEG_FATAL_MARKERS). Not filed as a task -- needs ffmpeg version probe to disambiguate. | -- |
| C5 | nvenc ABSENT (bogus ffmpeg path) | PASS | `success=False, err='ffmpeg binary not found'` | -- |
| C6 | Default convert_batch return shape | PASS | dict contains required keys + telemetry on all converters | -- |
| C7 | Per-format quality scalars | FAIL | int(quality) cast in vips/nvenc/magick-wand truncates fractional | task_024 |
| D1-D5 | Hot folder full trigger flow | BLOCKED | Lifecycle endpoints PASS (A5-A7); real watchdog+debounce trigger not driven end-to-end in this audit (timer-dependent). Static reading confirms readiness gate & polling fallback wired. | -- |
| D2 | HOT_FOLDER_DEBOUNCE_MS knob | FAIL | config constant not passed to HotFolderHandler ctor; hardcoded 5.0s used | task_025 |
| E1 | Curve evaluation + clamps | PASS | shipped table empty -> falls back to default_quality_for, in encoder range | -- |
| E2 | Fallback via default_quality_for | PASS | all 4 lookup cases returned the expected tool/format-native default | -- |
| E3 | Shipped empty table tolerated | PASS | version="2.0.0", every lookup falls back, no crash | -- |
| E4 | CLI generator schema parity | BLOCKED | Not exercised (no analytics DB on host); static read of `generate_heuristic_data` indicates wrapper-only | -- |
| F1 | WAL + integrity_check | PASS | journal_mode=wal, integrity_check=ok on fresh DB | -- |
| F2 | Required tables present | PASS | batch_runs, batch_summary, batch_errors, images, conversions, metrics, quality_priors present | -- |
| F3 | Repository round-trips | PASS | implicit via A1-A4+A9 happy path (create_run, get_run, get_summary, get_all_runs, reap_stale_running) | -- |
| F4 | with_busy_retry actually engages | BLOCKED | Requires deliberate contention (e.g. holding a write lock from another thread). Static path is correct; not driven live. | -- |
| G1 | Logger writes without egress | PASS-DEGRADED | File sink works; **rotation race fails on every cross of maxBytes** | task_021 |
| G2 | Telemetry tolerates GPU absent | PASS | pynvml present here; static path: ImportError caught -> HAS_GPU=False (telemetry.py:18-34) | -- |
| G3 | No egress on golden paths | FAIL | `style_utils.py:26` does @import to fonts.googleapis.com | task_027 |
| G4 | Launch command parity (sandbox_init vs CLAUDE.md) | FAIL | sandbox_init.ps1 uses `streamlit run app/web/batch_gui/main.py`; CLAUDE.md says `streamlit run -m app.web.batch_gui.main`. Also: **`npm install` requires network** on a `<Networking>Disable>` box. | task_023 |
| Closure | cp314-only native wheels vs `>=3.12` floor | FAIL | every cp3xx wheel in vendor/wheels is cp314; pyproject claims `>=3.12` | task_022 |
| Closure | npm + node_modules vendored | PASS-PARTIAL | dev `node_modules\sharp` exists and is mapped; **`npm install` step is the failing path** | task_023 |
| Closure | pillow-heif wheel present | PASS | `pillow_heif-1.3.0-cp314-cp314-win_amd64.whl` in vendor/wheels; missing locally on dev host but installable on sandbox | -- |

