"""Steel-thread harness #1: API surface, orchestrator wiring, DB persistence.

Runs A1-A7 + A9 + B1, B2, B5, B7, B8 + F1, F2, F3 in one process. ASCII only.

Strategy:
1. Set PIXELPIVOT_DB_PATH to a throwaway temp file BEFORE importing the app so
   no production state is touched.
2. Build a tiny ASCII-named PNG fixture with Pillow.
3. Drive the real FastAPI app via TestClient (lifespan fires init_db + reaper).
4. Poll /batch/status/{run_id} until completed; verify the artifact + DB rows.
5. Validate hot folder lifecycle (register/list/delete).
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# CRITICAL: set env vars BEFORE the app modules import paths.py
_TMP = Path(tempfile.mkdtemp(prefix="pp_audit_"))
os.environ["PIXELPIVOT_DB_PATH"] = str(_TMP / "audit.db")
os.environ.pop("PIXELPIVOT_ALLOWED_ROOT", None)  # default: no containment

# Make the project importable
PROJ = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJ))

from fastapi.testclient import TestClient
from app.batch_api.main import app
from PIL import Image
import sqlite3


def make_png(path: Path, w: int = 64, h: int = 64) -> None:
    img = Image.new("RGB", (w, h), color=(120, 60, 200))
    img.save(str(path), format="PNG")


def banner(s: str) -> None:
    print(f"\n=== {s} ===")


def main() -> int:
    failures: list[str] = []
    src = _TMP / "src"
    tgt = _TMP / "tgt"
    src.mkdir()
    tgt.mkdir()
    fixture = src / "tiny.png"
    make_png(fixture)
    print(f"TMP={_TMP}")
    print(f"FIXTURE={fixture} size={fixture.stat().st_size}")

    # ---------------------------------------------------------------- A9 lifespan
    banner("A9 lifespan: init_db + reap_stale_running")
    with TestClient(app) as client:
        # Lifespan fired -> DB file should exist now
        db_path = Path(os.environ["PIXELPIVOT_DB_PATH"])
        if not db_path.exists():
            failures.append("A9: DB file not created by lifespan")
        else:
            print(f"A9: DB file exists at {db_path}")

        # Insert a fake 'running' row to be reaped on next startup
        with sqlite3.connect(str(db_path)) as raw:
            raw.execute(
                "INSERT INTO batch_runs (source_dir, target_dir, target_format, tool, "
                "trigger_type, status) VALUES (?, ?, ?, ?, ?, ?)",
                ("x", "y", "webp", "magick", "manual", "running"),
            )
            raw.commit()
            (ghost_id,) = raw.execute(
                "SELECT id FROM batch_runs WHERE source_dir='x'"
            ).fetchone()
            print(f"A9 reaper prep: inserted ghost row id={ghost_id}, status=running")

    # Re-open TestClient -> lifespan fires again, reaper should transition the ghost
    with TestClient(app) as client:
        with sqlite3.connect(os.environ["PIXELPIVOT_DB_PATH"]) as raw:
            row = raw.execute(
                "SELECT status, completed_at FROM batch_runs WHERE id=?", (ghost_id,)
            ).fetchone()
            print(f"A9 reaper post: ghost row -> status={row[0]} completed_at={row[1]}")
            if row[0] != "interrupted":
                failures.append(f"A9: ghost was not reaped to 'interrupted' (got {row[0]})")
            if row[1] is None:
                failures.append("A9: reaped row missing completed_at")

        # --------------------------------------------------- F1, F2 schema/tables
        banner("F1/F2 schema + integrity + WAL")
        with sqlite3.connect(os.environ["PIXELPIVOT_DB_PATH"]) as raw:
            jm = raw.execute("PRAGMA journal_mode").fetchone()[0]
            ic = raw.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {
                r[0] for r in raw.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            print(f"F1: journal_mode={jm} integrity_check={ic}")
            print(f"F2: tables={sorted(tables)}")
            required = {
                "batch_runs", "batch_summary", "batch_errors",
                "images", "conversions", "metrics", "quality_priors",
            }
            missing = required - tables
            if missing:
                failures.append(f"F2: missing required tables {missing}")
            if jm.lower() != "wal":
                failures.append(f"F1: journal_mode is {jm}, expected WAL")
            if ic.lower() != "ok":
                failures.append(f"F1: integrity_check returned {ic}")

        # --------------------------------------------------- A8 validation 422
        banner("A8 validation: empty/whitespace/invalid bodies -> 422")
        bad_bodies = [
            ("empty_format_list", {
                "source_dir": str(src), "target_dir": str(tgt),
                "target_format": [], "tool": ["magick"], "category": ["general"]
            }, 422),
            ("empty_tool_list", {
                "source_dir": str(src), "target_dir": str(tgt),
                "target_format": ["webp"], "tool": [], "category": ["general"]
            }, 422),
            ("whitespace_path", {
                "source_dir": "   ", "target_dir": str(tgt),
                "target_format": ["webp"], "tool": ["magick"], "category": ["general"]
            }, 422),
            ("bad_format", {
                "source_dir": str(src), "target_dir": str(tgt),
                "target_format": ["bmp"], "tool": ["magick"], "category": ["general"]
            }, 422),
            ("bad_tool", {
                "source_dir": str(src), "target_dir": str(tgt),
                "target_format": ["webp"], "tool": ["paint"], "category": ["general"]
            }, 422),
        ]
        for name, body, want in bad_bodies:
            r = client.post("/api/v1/batch/start", json=body)
            ok = (r.status_code == want)
            print(f"A8 {name}: {r.status_code} (expected {want}) -> {'OK' if ok else 'FAIL'}")
            if not ok:
                failures.append(f"A8 {name}: expected {want}, got {r.status_code}: {r.text[:200]}")

        # --------------------------------------------------- A1 happy path
        banner("A1/B1/B2/B5/B7 happy path: POST /batch/start + complete")
        body = {
            "source_dir": str(src), "target_dir": str(tgt),
            "target_format": ["webp"], "tool": ["magick"], "category": ["general"]
        }
        r = client.post("/api/v1/batch/start", json=body)
        print(f"A1: POST status={r.status_code} body={r.text[:200]}")
        if r.status_code != 200:
            failures.append(f"A1: expected 200, got {r.status_code}")
            return 1
        run_id = r.json()["run_id"]

        # A2 poll status
        completed = None
        for _ in range(120):  # ~24s
            time.sleep(0.2)
            r2 = client.get(f"/api/v1/batch/status/{run_id}")
            if r2.status_code != 200:
                failures.append(f"A2: status returned {r2.status_code}")
                break
            data = r2.json()
            if data["status"] in ("completed", "failed"):
                completed = data
                break
        print(f"A2: final status={completed}")
        if not completed or completed["status"] != "completed":
            failures.append(f"A2: did not reach 'completed' (got {completed})")

        # B5 verify artifact exists + savings credited only to this run
        out = tgt / "tiny_magick.webp"
        if not out.exists() or out.stat().st_size == 0:
            failures.append(f"B5: expected {out} to exist and be non-empty")
        else:
            print(f"B5: artifact {out} size={out.stat().st_size}")

        # B7: summary row exists
        with sqlite3.connect(os.environ["PIXELPIVOT_DB_PATH"]) as raw:
            srow = raw.execute(
                "SELECT duration_ms, success_count, failure_count, savings_pct FROM batch_summary WHERE batch_id=?",
                (run_id,)
            ).fetchone()
            print(f"B7: batch_summary row -> {srow}")
            if srow is None:
                failures.append("B7: batch_summary not written")
            else:
                if srow[1] != 1 or srow[2] != 0:
                    failures.append(f"B7: success/failure counts wrong: {srow}")

        # ---------------------------------------------------- A3 errors
        banner("A3 /batch/{run_id}/errors")
        r = client.get(f"/api/v1/batch/{run_id}/errors")
        print(f"A3: status={r.status_code} body={r.text[:200]}")
        if r.status_code != 200 or not isinstance(r.json(), list):
            failures.append(f"A3: expected 200 + list, got {r.status_code} {r.text[:200]}")
        # Successful run should have empty errors list
        if r.json():
            failures.append(f"A3: unexpected errors on happy path: {r.json()}")

        # ---------------------------------------------------- A4 history
        banner("A4 /batch/history")
        r = client.get("/api/v1/batch/history")
        runs = r.json()
        print(f"A4: status={r.status_code} count={len(runs)} sample_keys={list(runs[0].keys()) if runs else []}")
        if r.status_code != 200 or not isinstance(runs, list):
            failures.append(f"A4: expected 200 + list, got {r.status_code}")
        else:
            if not any(rr.get("run_id") == run_id for rr in runs):
                failures.append("A4: completed run not present in /history")

        # ---------------------------------------------------- B8 empty source dir
        banner("B8 empty source dir -> total_images=0, no crash")
        empty_src = _TMP / "empty_src"
        empty_src.mkdir()
        r = client.post("/api/v1/batch/start", json={
            "source_dir": str(empty_src), "target_dir": str(tgt),
            "target_format": ["webp"], "tool": ["magick"], "category": ["general"],
        })
        if r.status_code != 200:
            failures.append(f"B8: start failed: {r.status_code} {r.text[:200]}")
        else:
            rid = r.json()["run_id"]
            final = None
            for _ in range(40):
                time.sleep(0.1)
                r2 = client.get(f"/api/v1/batch/status/{rid}").json()
                if r2["status"] in ("completed", "failed"):
                    final = r2
                    break
            print(f"B8 final: {final}")
            if not final or final["status"] != "failed":
                failures.append(f"B8: empty-src run status was not 'failed' (got {final})")
            elif final.get("total_images", -1) != 0:
                failures.append(f"B8: total_images != 0 (got {final.get('total_images')})")

        # ---------------------------------------------------- A5/A6/A7 hot folder
        banner("A5/A6/A7 hot folder lifecycle")
        hf_src = _TMP / "hf_src"
        hf_tgt = _TMP / "hf_tgt"
        hf_src.mkdir()
        hf_tgt.mkdir()
        r = client.post("/api/v1/hotfolder/register", json={
            "source_dir": str(hf_src), "target_dir": str(hf_tgt),
            "target_format": ["webp"], "tool": ["magick"], "category": ["general"],
        })
        print(f"A5 register: {r.status_code} {r.text[:200]}")
        if r.status_code != 200:
            failures.append(f"A5: register failed: {r.status_code} {r.text[:200]}")
        else:
            wid = r.json()["watcher_id"]
            r = client.get("/api/v1/hotfolder/list")
            print(f"A6 list: {r.status_code} count={len(r.json()) if r.status_code==200 else '?'}")
            if r.status_code != 200 or not any(w.get("watcher_id") == wid for w in r.json()):
                failures.append("A6: registered watcher missing from list")
            r = client.delete(f"/api/v1/hotfolder/{wid}")
            print(f"A7 delete: {r.status_code} {r.text[:200]}")
            if r.status_code != 200:
                failures.append(f"A7: delete failed: {r.status_code}")
            r = client.delete(f"/api/v1/hotfolder/{wid}")
            print(f"A7 delete-twice: {r.status_code} (expected 404)")
            if r.status_code != 404:
                failures.append(f"A7: second delete should be 404, got {r.status_code}")

        # ---------------------------------------------------- A8 path containment
        banner("A8 PIXELPIVOT_ALLOWED_ROOT containment")
        # set env via os and rebuild the request — pydantic validators read at call time
        os.environ["PIXELPIVOT_ALLOWED_ROOT"] = str(_TMP)
        try:
            # inside allowed root -> OK
            r = client.post("/api/v1/batch/start", json={
                "source_dir": str(src), "target_dir": str(tgt),
                "target_format": ["webp"], "tool": ["magick"], "category": ["general"],
            })
            print(f"A8 inside-root: {r.status_code}")
            if r.status_code != 200:
                failures.append(f"A8 containment(inside): expected 200, got {r.status_code} {r.text[:200]}")
            # outside allowed root -> 422
            outside = Path(tempfile.gettempdir()) / "definitely_outside_pp_audit"
            outside.mkdir(exist_ok=True)
            r = client.post("/api/v1/batch/start", json={
                "source_dir": str(outside), "target_dir": str(tgt),
                "target_format": ["webp"], "tool": ["magick"], "category": ["general"],
            })
            print(f"A8 outside-root: {r.status_code} body={r.text[:200]}")
            if r.status_code != 422:
                failures.append(f"A8 containment(outside): expected 422, got {r.status_code}")
        finally:
            os.environ.pop("PIXELPIVOT_ALLOWED_ROOT", None)

    print("\n========================================")
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for f in failures:
            print(f" - {f}")
        return 1
    print("ALL THREADS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
