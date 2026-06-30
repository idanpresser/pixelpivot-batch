# Epic E3: Error Handling + Resilience — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One corrupt file, a lost Sharp daemon, or DB lock contention must never sink a batch — isolate the failure, fall back or retry, keep processing.

**Architecture:** Three independent guards. (e3.1) The existing `with_db_retry` exponential-backoff decorator (`connection.py:343`) is verified and applied to the uncovered write paths. (e3.2) A DLQ step in the orchestrator batch loop moves corrupt inputs to `corrupt_or_failed/` and records them in `batch_errors` with a `dlq` marker, then continues. (e3.3) `SharpConverter` catches daemon timeout/disconnect and retries the affected files through `VipsConverter`, overwriting the recorded `tool` to `vips`.

**Tech Stack:** existing `with_db_retry`, `sqlite3.OperationalError`, `VipsConverter`, orchestrator batch loop, `batch_errors` table.

**Spec:** `docs/superpowers/specs/2026-06-30-production-readiness-design.md` (E3).
**Beads:** epic `pixelpivot_batch-34i`; children `.1` (e3.1), `.2` (e3.2), `.3` (e3.3).

**Pre-work discovery (2026-06-30):**
- `with_db_retry(func=None, *, max_retries=5, initial_delay=0.1)` already exists at `connection.py:343` — exponential (`delay *= 2`), retries only on `sqlite3.OperationalError` containing `locked`/`busy`, re-raises everything else. **Postgres is automatically a no-op** (psycopg raises `psycopg.errors.*`, not `sqlite3.OperationalError`, so it hits the non-retryable branch). e3.1 is therefore *verify + apply coverage + lock-in test*, not build-from-scratch.
- Batch path records failures in `batch_errors` (schema), NOT `conversions` — per CLAUDE.md "no per-image DB row in the batch path." DLQ uses `batch_errors`.
- Orchestrator: `execute_batch` at `orchestrator.py:262`, converter dispatch `convert_batch(...)` at `:410`.

---

## File Structure

- **Modify** `app/core/db/connection.py` — only if a write site needs the decorator and a re-export is cleaner; likely no change beyond applying `@with_db_retry`.
- **Modify** `app/core/db/repositories/batch.py` (+ other write-heavy repos) — wrap the mutating methods with `@with_db_retry`.
- **Modify** `app/batch_api/orchestrator.py` — DLQ isolation around the per-file/per-chunk convert path in `execute_batch`.
- **Modify** `app/core/converters/sharp_converter.py` — daemon-failure fallback to `VipsConverter`, `tool` overwrite.
- **Create** `tests/db/test_db_retry_coverage.py`, `tests/test_dlq.py`, `tests/converters/test_sharp_vips_fallback.py`.

---

## Task 1 (e3.1): verify + apply DB retry coverage

**Files:**
- Modify: `app/core/db/repositories/batch.py` (and peers with mutating writes)
- Test: `tests/db/test_db_retry_coverage.py`

- [ ] **Step 1: Write the failing test** — prove retry behavior + postgres no-op contract

```python
# tests/db/test_db_retry_coverage.py
import sqlite3
import pytest
from app.core.db.connection import with_db_retry


def test_retries_on_locked_then_succeeds():
    calls = {"n": 0}

    @with_db_retry(max_retries=3, initial_delay=0.0)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_non_lock_operationalerror_not_retried():
    calls = {"n": 0}

    @with_db_retry(max_retries=3, initial_delay=0.0)
    def boom():
        calls["n"] += 1
        raise sqlite3.OperationalError("no such table: x")

    with pytest.raises(sqlite3.OperationalError):
        boom()
    assert calls["n"] == 1  # not retried


def test_non_sqlite_error_is_passthrough():
    # simulates a postgres psycopg OperationalError: different class -> no retry
    class PgOperationalError(Exception):
        pass

    calls = {"n": 0}

    @with_db_retry(max_retries=3, initial_delay=0.0)
    def pg():
        calls["n"] += 1
        raise PgOperationalError("connection reset")

    with pytest.raises(PgOperationalError):
        pg()
    assert calls["n"] == 1  # postgres no-op contract
```

- [ ] **Step 2: Run — expect PASS already** (decorator exists)

Run: `pytest tests/db/test_db_retry_coverage.py -v`
Expected: PASS. These lock in the existing behavior + the postgres no-op contract. If any fail, the decorator regressed — fix `connection.py:343` before proceeding.

- [ ] **Step 3: Audit write sites + apply decorator where missing**

Run: `pytest -q` first (baseline green). Then grep mutating repo methods:

```bash
git grep -nE "def (create_|insert_|update_|record_|mark_|finalize_|save_)" app/core/db/repositories
```

For each method that performs an `INSERT`/`UPDATE`/`DELETE` and is NOT already inside a `with_db_retry`-wrapped caller, add the decorator:

```python
from ..connection import with_db_retry

@with_db_retry
def create_run(self, conn, *, source_dir, target_dir, target_format, tool, trigger_type, heuristic_version):
    ...  # body unchanged
```

> Only wrap the outermost write method, not helpers it calls — double-wrapping multiplies retries. Read paths don't need it.

- [ ] **Step 4: Run full suite**

Run: `pytest -q`
Expected: PASS (no behavior change on sqlite; decorator is transparent on success).

- [ ] **Step 5: Commit**

```bash
git add app/core/db/repositories tests/db/test_db_retry_coverage.py
git commit -m "feat(db): lock in with_db_retry contract + apply to uncovered write paths (e3.1)"
```

---

## Task 2 (e3.2): DLQ for corrupt files

**Files:**
- Modify: `app/batch_api/orchestrator.py` (`execute_batch`, ~`:262`–`:410`)
- Test: `tests/test_dlq.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dlq.py
from pathlib import Path
from app.batch_api.orchestrator import quarantine_to_dlq


def test_quarantine_moves_file_and_returns_record(tmp_path):
    target = tmp_path / "out"
    target.mkdir()
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n garbage")  # ASCII only, per project rule

    rec = quarantine_to_dlq(str(bad), str(target), reason="Corrupt PNG chunk")

    dlq_path = target / "corrupt_or_failed" / "broken.png"
    assert dlq_path.exists()           # moved, not copied
    assert not bad.exists()
    assert rec["path"].endswith("broken.png")
    assert rec["reason"] == "Corrupt PNG chunk"
    assert rec["dlq"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dlq.py -v`
Expected: FAIL — `quarantine_to_dlq` not defined.

- [ ] **Step 3: Implement the DLQ helper + wire into the batch loop**

Add to `app/batch_api/orchestrator.py`:

```python
import shutil
from pathlib import Path

def quarantine_to_dlq(in_path: str, target_dir: str, reason: str) -> dict:
    """Move a failed input into <target_dir>/corrupt_or_failed/ and return a batch_errors record."""
    dlq_dir = Path(target_dir) / "corrupt_or_failed"
    dlq_dir.mkdir(parents=True, exist_ok=True)
    dest = dlq_dir / Path(in_path).name
    try:
        shutil.move(in_path, dest)
    except FileNotFoundError:
        pass  # already gone; still record the failure
    return {"path": str(dest), "reason": reason, "dlq": True}
```

In `execute_batch`, where per-file/chunk conversion errors are collected, route corrupt-input errors through the helper and persist to `batch_errors` with the `dlq` marker, then continue the loop (do not raise):

```python
for err in result.get("errors", []):
    rec = quarantine_to_dlq(err["path"], request.target_dir, reason=err.get("error", "conversion failed"))
    repo.record_error(conn, run_id=run_id, image_path=rec["path"],
                      message=rec["reason"], is_dlq=True)
    log.warning("file quarantined to DLQ", extra={"subprocess": {"path": rec["path"], "reason": rec["reason"]}})
```

> If `BatchRepository.record_error` lacks an `is_dlq` flag, add a nullable `is_dlq INTEGER DEFAULT 0` to the `batch_errors` DDL in `schema.py` (both `_SQLITE_DDL` and `_POSTGRES_DDL`) and thread it through `record_error`. Keep the trace_id (E1) on the log line automatically via the filter.

- [ ] **Step 4: Run test + batch suite**

Run: `pytest tests/test_dlq.py tests/ -k "orchestrator or batch" -q`
Expected: PASS — one corrupt file isolated, batch completes.

- [ ] **Step 5: Commit**

```bash
git add app/batch_api/orchestrator.py app/core/db/schema.py app/core/db/repositories/batch.py tests/test_dlq.py
git commit -m "feat(resilience): DLQ quarantine for corrupt files; batch continues (e3.2)"
```

---

## Task 3 (e3.3): Sharp→Vips fallback

**Files:**
- Modify: `app/core/converters/sharp_converter.py` (`convert`, `convert_batch`)
- Test: `tests/converters/test_sharp_vips_fallback.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/converters/test_sharp_vips_fallback.py
import socket
from app.core.converters.sharp_converter import SharpConverter


def test_daemon_failure_falls_back_to_vips(monkeypatch, tmp_path):
    src = tmp_path / "a.png"
    src.write_bytes(b"fake")
    out = tmp_path / "a.webp"

    conv = SharpConverter()

    # force the daemon path to raise a disconnect
    def dead_socket(*a, **k):
        raise socket.error("daemon gone")
    monkeypatch.setattr(conv, "_send_to_daemon", dead_socket, raising=False)

    called = {"vips": False}
    def fake_vips_convert(self, in_path, out_path, fmt, q, run_id=None):
        called["vips"] = True
        return {"success": True, "tool": "vips", "bytes_written": 10}
    monkeypatch.setattr("app.core.converters.vips_converter.VipsConverter.convert", fake_vips_convert, raising=False)

    res = conv.convert(str(src), str(out), "webp", 80)
    assert called["vips"] is True
    assert res["success"] is True
    assert res["tool"] == "vips"   # tool overwritten, not left as 'sharp'
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/converters/test_sharp_vips_fallback.py -v`
Expected: FAIL — no fallback; socket error propagates.

- [ ] **Step 3: Implement fallback**

In `sharp_converter.py`, wrap the daemon send/recv in `convert` (and the batch loop in `convert_batch`) so a `socket.error`/timeout triggers a single retry via `VipsConverter`, with the result's `tool` overwritten:

```python
import socket
from .vips_converter import VipsConverter

def convert(self, in_path, out_path, target_format, quality, run_id=None):
    try:
        return self._convert_via_daemon(in_path, out_path, target_format, quality, run_id=run_id)
    except (socket.error, socket.timeout, ConnectionError) as e:
        log.warning("sharp daemon unavailable, falling back to vips",
                    extra={"subprocess": {"error": str(e), "in_path": in_path}})
        res = VipsConverter().convert(in_path, out_path, target_format, quality, run_id=run_id)
        if isinstance(res, dict):
            res["tool"] = "vips"           # overwrite for accurate metrics history
            res["fallback_from"] = "sharp"
        return res
```

Refactor the existing daemon send/recv body of `convert` into `_convert_via_daemon` (pure move, no logic change). For `convert_batch`, on daemon failure retry only the un-converted files through `VipsConverter.convert_batch` and merge counts; tag the converted rows `tool="vips"`.

> Do NOT roll back any partial Sharp DB write — overwrite the `tool` field on the successful vips save (spec e3.3). Keeps metrics accurate without orphaned rows.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/converters/test_sharp_vips_fallback.py -v`
Expected: PASS.

- [ ] **Step 5: Run converter suite**

Run: `pytest tests/ -k "sharp or vips or converter" -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/core/converters/sharp_converter.py tests/converters/test_sharp_vips_fallback.py
git commit -m "feat(resilience): sharp->vips fallback on daemon loss; tool overwritten (e3.3)"
```

---

## Task 4: Full regression + epic close

- [ ] **Step 1:** Run `pytest -q` — expect green (pre-existing streamlit-guard failures excepted).
- [ ] **Step 2:** Postgres leg — `$env:PIXELPIVOT_DB_URL="postgresql+psycopg://pixelpivot:pixelpivot@localhost:5433/pixelpivot_test"; pytest -q` — confirm retry no-op + DLQ schema both-dialect.
- [ ] **Step 3:** Close beads:

```bash
bd close pixelpivot_batch-34i.1 pixelpivot_batch-34i.2 pixelpivot_batch-34i.3 pixelpivot_batch-34i
```

- [ ] **Step 4:** Open PR for the epic branch.

---

## Self-Review

**Spec coverage (E3):**
- e3.1 backoff retry, postgres no-op → Task 1 (verify existing + apply coverage). ✓ Catch-type corrected to raw `sqlite3.OperationalError` (facade uses `raw_connection()`, not SQLAlchemy-wrapped exceptions) — supersedes the spec's earlier `sqlalchemy.exc.OperationalError` note.
- e3.2 DLQ dir + DB flag + continue → Task 2. ✓ Uses `batch_errors` (batch path has no per-image `conversions` row).
- e3.3 Sharp→Vips fallback, tool overwrite, no rollback → Task 3. ✓

**Placeholder scan:** Task 1 Step 3 and Task 3 Step 3 reference "peer repos" / "existing daemon body" — these are concrete refactors (grep given; method named `_convert_via_daemon`). Task 2 notes a conditional schema add (`is_dlq`) with exact DDL location. No bare TODOs.

**Type/name consistency:** `with_db_retry`, `quarantine_to_dlq`, `_convert_via_daemon`, `record_error(..., is_dlq=...)`, `tool="vips"` — consistent across tasks.

**Discrepancy flagged for the executor:** the spec's e3.1 acceptance says "catches `sqlalchemy.exc.OperationalError`." That was written before E2 chose the `raw_connection()` facade. The CORRECT target is `sqlite3.OperationalError` (what `with_db_retry` already catches). Task 1 reflects the corrected contract.
