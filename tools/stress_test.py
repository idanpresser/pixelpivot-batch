"""Stress Test — concurrent batch conversion harness for load testing.

Tests the API under parallel batch submission and monitors for database
concurrency and resource contention issues.
"""

import httpx
import asyncio
import time
import os
import tempfile
import shutil
from pathlib import Path

API_URL = os.getenv("BATCH_API_URL", "http://localhost:8000/api/v1")


async def run_stress_batch(batch_id: int, source_dir: str):
    """Run a single stress test batch against the API.

    Args:
        batch_id: Numeric batch identifier for logging.
        source_dir: Directory containing source images for conversion.
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        target_dir = tempfile.mkdtemp(prefix=f"stress_out_{batch_id}_")
        payload = {
            "source_dir": source_dir,
            "target_dir": target_dir,
            "target_format": "webp",
            "tool": "magick",
            "category": "general"
        }
        
        print(f"Starting Batch {batch_id}...")
        response = await client.post(f"{API_URL}/batch/start", json=payload)
        if response.status_code != 200:
            print(f"Batch {batch_id} failed to start: {response.text}")
            return
        
        run_id = response.json()["run_id"]
        
        # Poll for completion
        while True:
            status_resp = await client.get(f"{API_URL}/batch/status/{run_id}")
            status = status_resp.json()["status"]
            if status == "completed":
                print(f"Batch {batch_id} (Run {run_id}) COMPLETED.")
                break
            elif status == "failed":
                print(f"Batch {batch_id} (Run {run_id}) FAILED.")
                break
            await asyncio.sleep(2)
        
        shutil.rmtree(target_dir)

async def main():
    """Main stress test harness: spawn concurrent batch jobs and measure duration."""
    # Setup stress source
    stress_src = tempfile.mkdtemp(prefix="stress_src_")
    # Create 10 dummy images (or use existing test assets if available)
    # For a real stress test we'd use more, but this is a structural proof.
    for i in range(10):
        Path(stress_src, f"image_{i}.jpg").write_text("dummy") # This will fail real conversion but test DB concurrency
    
    tasks = [run_stress_batch(i, stress_src) for i in range(5)]
    start = time.time()
    await asyncio.gather(*tasks)
    print(f"Total stress test duration: {time.time() - start:.2f}s")
    
    shutil.rmtree(stress_src)

if __name__ == "__main__":
    if "BATCH_API_URL" not in os.environ:
        print("Warning: BATCH_API_URL not set, using localhost:8000")
    asyncio.run(main())
