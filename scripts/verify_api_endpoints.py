"""One-shot API endpoint verifier.

Exercises every /api/v1 route against a live backend on http://127.0.0.1:8000
and writes a compact JSON record of {request, status_code, body} per call to
out/api_verify.json. Used to ground docs/API_REFERENCE.md examples in real
responses. Big arrays are truncated to keep the output token-cheap.

Run:  .venv\\Scripts\\python.exe scripts\\verify_api_endpoints.py
"""
import json
import shutil
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
API = f"{BASE}/api/v1"
ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "image_samples"
TMP_SRC = ROOT / "scratch" / "api_verify_src"
TMP_OUT = ROOT / "scratch" / "api_verify_out"
HOT_SRC = ROOT / "scratch" / "api_verify_hot_src"
HOT_OUT = ROOT / "scratch" / "api_verify_hot_out"
RESULTS = ROOT / "out" / "api_verify.json"

records = []


def trunc(body, max_list=2):
    """Shrink large arrays so the captured body stays small."""
    if isinstance(body, list):
        head = [trunc(x, max_list) for x in body[:max_list]]
        if len(body) > max_list:
            head.append(f"... (+{len(body) - max_list} more, {len(body)} total)")
        return head
    if isinstance(body, dict):
        return {k: trunc(v, max_list) for k, v in body.items()}
    return body


def rec(label, method, url, resp, req_body=None):
    try:
        body = resp.json()
    except Exception:
        body = resp.text
    records.append({
        "label": label,
        "method": method,
        "url": url.replace(BASE, ""),
        "request_body": req_body,
        "status_code": resp.status_code,
        "body": trunc(body),
    })
    return body


def setup_sources():
    for d in (TMP_SRC, TMP_OUT, HOT_SRC, HOT_OUT):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    imgs = sorted(p for p in SRC.iterdir() if p.suffix.lower() == ".png")[:2]
    for p in imgs:
        shutil.copy(p, TMP_SRC / p.name)
    return len(imgs)


def poll_until_done(client, run_id, timeout=120):
    """Poll status; return final body. Captures one mid-flight progress sample."""
    progress_captured = False
    deadline = time.time() + timeout
    final = None
    while time.time() < deadline:
        r = client.get(f"{API}/batch/status/{run_id}")
        body = r.json()
        if not progress_captured and body.get("status") in ("running", "queued"):
            pr = client.get(f"{API}/batch/{run_id}/progress")
            if pr.status_code == 200:
                rec("GET batch/{id}/progress (in-flight)", "GET",
                    f"{API}/batch/{run_id}/progress", pr)
                progress_captured = True
        if body.get("status") in ("completed", "failed", "cancelled"):
            final = (r, body)
            break
        time.sleep(0.5)
    return final


def main():
    n = setup_sources()
    with httpx.Client(timeout=30) as client:
        # 1. root
        rec("GET / (root health)", "GET", f"{BASE}/", client.get(f"{BASE}/"))

        # 2. start a real batch (2 imgs x vips x webp = 2 conversions)
        start_req = {
            "source_dir": str(TMP_SRC),
            "target_dir": str(TMP_OUT),
            "target_format": ["webp"],
            "tool": ["vips"],
            "category": ["general"],
            "trigger_type": "manual",
        }
        r = client.post(f"{API}/batch/start", json=start_req)
        start_body = rec("POST batch/start", "POST", f"{API}/batch/start", r, start_req)
        run_id = start_body["run_id"]

        # 3. control while in-flight (best-effort; tiny batch may already be done)
        for action in ("pause", "resume"):
            cr = client.post(f"{API}/batch/{run_id}/control", json={"action": action})
            rec(f"POST batch/{{id}}/control ({action})", "POST",
                f"{API}/batch/{run_id}/control", cr, {"action": action})

        # 4. status polling + in-flight progress
        final = poll_until_done(client, run_id)
        if final:
            rec("GET batch/status/{id} (completed)", "GET",
                f"{API}/batch/status/{run_id}", final[0])

        # 5. errors
        er = client.get(f"{API}/batch/{run_id}/errors")
        rec("GET batch/{id}/errors", "GET", f"{API}/batch/{run_id}/errors", er)

        # 6. progress after completion (expect 404)
        pr = client.get(f"{API}/batch/{run_id}/progress")
        rec("GET batch/{id}/progress (after done -> 404)", "GET",
            f"{API}/batch/{run_id}/progress", pr)

        # 7. control on finished run (expect 404, no active control)
        cr = client.post(f"{API}/batch/{run_id}/control", json={"action": "stop"})
        rec("POST batch/{id}/control (finished -> 404)", "POST",
            f"{API}/batch/{run_id}/control", cr, {"action": "stop"})

        # 8. status of unknown run (expect 404)
        nr = client.get(f"{API}/batch/status/999999")
        rec("GET batch/status/{id} (unknown -> 404)", "GET",
            f"{API}/batch/status/999999", nr)

        # 9. restart
        rr = client.post(f"{API}/batch/{run_id}/restart")
        restart_body = rec("POST batch/{id}/restart", "POST",
                           f"{API}/batch/{run_id}/restart", rr)
        poll_until_done(client, restart_body["run_id"])

        # 10. history
        hr = client.get(f"{API}/batch/history")
        rec("GET batch/history", "GET", f"{API}/batch/history", hr)

        # 11. hotfolder register / list / delete
        hot_req = {
            "source_dir": str(HOT_SRC),
            "target_dir": str(HOT_OUT),
            "target_format": ["webp"],
            "tool": ["vips"],
            "category": ["general"],
        }
        hreg = client.post(f"{API}/hotfolder/register", json=hot_req)
        hreg_body = rec("POST hotfolder/register", "POST",
                        f"{API}/hotfolder/register", hreg, hot_req)
        watcher_id = hreg_body.get("watcher_id")

        rec("GET hotfolder/list", "GET", f"{API}/hotfolder/list",
            client.get(f"{API}/hotfolder/list"))

        rec("DELETE hotfolder/{id}", "DELETE", f"{API}/hotfolder/{watcher_id}",
            client.delete(f"{API}/hotfolder/{watcher_id}"))

        rec("DELETE hotfolder/{id} (unknown -> 404)", "DELETE",
            f"{API}/hotfolder/does-not-exist",
            client.delete(f"{API}/hotfolder/does-not-exist"))

        # 12. validation error sample (empty tool list -> 422)
        bad = dict(start_req)
        bad["tool"] = []
        rec("POST batch/start (invalid -> 422)", "POST", f"{API}/batch/start",
            client.post(f"{API}/batch/start", json=bad), {"tool": "[]"})

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(records, indent=2, default=str), encoding="utf-8")
    print(f"wrote {len(records)} records ({n} src imgs) -> {RESULTS}")


if __name__ == "__main__":
    main()
