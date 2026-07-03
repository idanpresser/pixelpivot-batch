#!/usr/bin/env python3
"""E2E Docker matrix test script for PixelPivot Batch Engine.

Triggers batch runs via the FastAPI REST API, polls for status, and reports metrics.
"""
import os
import sys
import time
import shutil
from pathlib import Path
import httpx

API_BASE = "http://pixelpivot-batch-api:8000/api/v1"
TOKEN = "dev_secret_token_change_me"
SOURCE_DIR = "/app/test_pics/flat"
TARGET_BASE = "/app/test_pics"
FORMATS = ["avif"]  # Switch to avif for 5-way comparison
TOOLS = ["magick", "vips", "sharp", "ffmpeg", "cavif"]

headers = {
    "X-API-Token": TOKEN,
    "Content-Type": "application/json"
}

def clean_dir(path: Path):
    """Clean and recreate a directory."""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)

def run_batch(label: str, request_data: dict) -> dict:
    """Submit a batch request, poll until completion, and return summary."""
    print(f"\n==================================================")
    print(f"🚀 STARTING BATCH: {label}")
    print(f"==================================================")
    print(f"Payload: {request_data}")
    
    with httpx.Client(timeout=30.0) as client:
        # 1. Start batch
        try:
            resp = client.post(f"{API_BASE}/batch/start", json=request_data, headers=headers)
            if resp.status_code != 200:
                print(f"❌ Failed to start batch. Status code: {resp.status_code}")
                print(f"Response: {resp.text}")
                return {"label": label, "status": "failed_start", "error": resp.text}
        except Exception as e:
            print(f"❌ Connection error when starting batch: {e}")
            return {"label": label, "status": "failed_connection", "error": str(e)}
        
        start_data = resp.json()
        run_id = start_data.get("run_id")
        total_images = start_data.get("total_images", 0)
        print(f"✅ Batch started successfully. run_id = {run_id}, total_images = {total_images}")
        
        # 2. Poll status
        start_time = time.perf_counter()
        timeout = 300  # 5 minutes timeout per run
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            try:
                status_resp = client.get(f"{API_BASE}/batch/status/{run_id}", headers=headers)
                if status_resp.status_code == 200:
                    status_data = status_resp.json()
                    status = status_data.get("status")
                    print(f"⏳ Polling: status = {status}")
                    
                    if status in ("completed", "failed", "cancelled"):
                        duration = time.perf_counter() - start_time
                        summary = status_data.get("summary") or {}
                        success_count = summary.get("success_count", 0)
                        failure_count = summary.get("failure_count", 0)
                        cpu_avg = summary.get("cpu_avg", 0.0)
                        ram_peak = summary.get("ram_peak", 0.0)
                        savings = summary.get("savings_pct", 0.0)
                        
                        # Get errors if any
                        errors = []
                        if failure_count > 0:
                            err_resp = client.get(f"{API_BASE}/batch/{run_id}/errors", headers=headers)
                            if err_resp.status_code == 200:
                                errors = err_resp.json()
                                
                        return {
                            "label": label,
                            "status": status,
                            "run_id": run_id,
                            "total_images": total_images,
                            "success_count": success_count,
                            "failure_count": failure_count,
                            "duration_s": duration,
                            "cpu_avg": cpu_avg,
                            "ram_peak_mb": ram_peak,
                            "savings_pct": savings,
                            "errors": errors
                        }
            except Exception as e:
                print(f"⚠️ Exception during polling: {e}")
            
            time.sleep(2.0)
            
        return {"label": label, "status": "timeout", "error": f"Batch timed out after {timeout}s"}

def main():
    # Verify source directory exists
    src_path = Path(SOURCE_DIR)
    if not src_path.exists() or not any(src_path.iterdir()):
        print(f"❌ Error: Source directory '{SOURCE_DIR}' is empty or does not exist.")
        sys.exit(1)
        
    print(f"📁 Source directory verified: {SOURCE_DIR}")
    print(f"🔍 Files count: {len(list(src_path.glob('*')))}")
    
    results = []
    
    # 1. Run each tool one after another
    for tool in TOOLS:
        target_dir = f"{TARGET_BASE}/out_{tool}"
        clean_dir(Path(target_dir))
        
        request_data = {
            "source_dir": SOURCE_DIR,
            "target_dir": target_dir,
            "target_format": FORMATS,
            "tool": [tool],
            "category": ["general"],
            "trigger_type": "e2e_test_single"
        }
        
        res = run_batch(f"Tool: {tool}", request_data)
        results.append(res)
        
    # 2. Run all tools together
    target_dir_all = f"{TARGET_BASE}/out_all_together"
    clean_dir(Path(target_dir_all))
    
    request_data_all = {
        "source_dir": SOURCE_DIR,
        "target_dir": target_dir_all,
        "target_format": FORMATS,
        "tool": TOOLS,
        "category": ["general"],
        "trigger_type": "e2e_test_all_together"
    }
    
    res_all = run_batch("All Together", request_data_all)
    results.append(res_all)
    
    # 3. Print Summary Table
    print("\n" + "="*80)
    print("📋 E2E TEST SUMMARY MATRIX")
    print("="*80)
    print(f"{'Run Label':<18} | {'Status':<10} | {'Success':<7} | {'Fail':<5} | {'Time (s)':<8} | {'CPU%':<5} | {'RAM (MB)':<8} | {'Savings%':<8}")
    print("-"*80)
    
    has_failed_runs = False
    for r in results:
        status = r.get("status", "unknown")
        label = r.get("label", "unknown")
        
        if status != "completed":
            has_failed_runs = True
            
        if status in ("completed", "failed"):
            success = r.get("success_count", 0)
            fail = r.get("failure_count", 0)
            duration = r.get("duration_s", 0.0)
            cpu = r.get("cpu_avg", 0.0)
            ram = r.get("ram_peak_mb", 0.0)
            savings = r.get("savings_pct", 0.0)
            print(f"{label:<18} | {status:<10} | {success:<7} | {fail:<5} | {duration:<8.2f} | {cpu:<5.1f} | {ram:<8.1f} | {savings:<8.1f}")
            if fail > 0:
                has_failed_runs = True
                print(f"   ⚠️ Errors for {label}:")
                for err in r.get("errors", [])[:5]:  # print first 5 errors
                    print(f"     - {err.get('path')}: {err.get('error')}")
                if len(r.get("errors", [])) > 5:
                    print(f"     - ... and {len(r.get('errors', [])) - 5} more errors")
        else:
            print(f"{label:<18} | {status:<10} | {'-':<7} | {'-':<5} | {'-':<8} | {'-':<5} | {'-':<8} | {'-':<8}")
            print(f"   ❌ Error detail: {r.get('error')}")
            
    print("="*80)
    
    if has_failed_runs:
        print("❌ E2E Matrix Tests completed with failures.")
        sys.exit(1)
    else:
        print("🎉 All E2E Matrix Tests completed successfully!")
        sys.exit(0)

if __name__ == "__main__":
    main()
