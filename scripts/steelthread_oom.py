"""Steel thread: reproduce + diagnose the system-wide OOM safely.

Background
----------
During air-gap E2E testing the machine hit a system-wide OOM and force-reset.
Suspected root cause lives in app/core/engine/phases/calibration.py:

  _determine_concurrency(self.images) throttles workers to 2 ONLY when
  has_massive is true. has_massive reads img["width"]/["height"], but those
  keys are populated into a SEPARATE mapping by _register_images(), not back
  into self.images. So when the image list enters calibration unprobed
  (width/height absent -> 0), the 56MP edge image passes the guard and the
  pool spawns cpu_count//2 worker PROCESSES, each decoding+encoding a full
  56MP frame. N workers x ~hundreds of MB -> RAM+pagefile exhaustion -> reset.

This script proves both halves WITHOUT repeating the reset:

  Probe 1  (pure, instant): feed _determine_concurrency unprobed dicts and
           show the guard does NOT throttle -> the bug, with zero risk.
  Probe 2  (bounded, capped): run a spawn ProcessPoolExecutor that decodes the
           56MP image, under a hard process-tree RAM watchdog that kills the
           whole tree the instant it crosses CAP_MB. Measures real per-worker
           and tree-peak RSS so the blast radius is quantified, not guessed.

SAFETY: every multiprocessing entrypoint is under `if __name__ == "__main__"`
(mandatory on Windows spawn) and the watchdog guarantees the tree is killed
long before the OS is starved. Run it; it cannot reset the box.

Usage:
    python scripts/steelthread_oom.py
    python scripts/steelthread_oom.py --cap-mb 2000 --workers 4 --huge-mp 56
"""

from __future__ import annotations

import argparse
import io
import os
import sys
import time
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed

import psutil

# --- Defaults -----------------------------------------------------------------
DEFAULT_CAP_MB   = 2500    # hard process-tree ceiling; watchdog kills above this
DEFAULT_HUGE_MP  = 56      # megapixels of the synthetic poison-pill image
DEFAULT_WORKERS  = None    # None -> mirror the buggy cpu_count // 2
SAMPLE_INTERVAL  = 0.20    # seconds between watchdog RSS samples


# --- Process-tree RAM watchdog ------------------------------------------------
class TreeMemoryWatchdog(threading.Thread):
    """Samples total RSS of this process + all descendants.

    If the total crosses cap_mb, it kills every descendant immediately so the
    OS is never starved. This is the safety valve that lets us reproduce the
    OOM path without another machine reset.
    """

    def __init__(self, cap_mb: int, interval: float = SAMPLE_INTERVAL):
        super().__init__(daemon=True)
        self.cap_mb = cap_mb
        self.interval = interval
        self.peak_mb = 0.0
        self.tripped = False
        self._stop = threading.Event()
        self._root = psutil.Process()

    def _tree_rss_mb(self) -> float:
        total = 0
        try:
            procs = [self._root] + self._root.children(recursive=True)
        except psutil.Error:
            procs = [self._root]
        for p in procs:
            try:
                total += p.memory_info().rss
            except psutil.Error:
                pass
        return total / (1024 * 1024)

    def _kill_descendants(self) -> None:
        try:
            kids = self._root.children(recursive=True)
        except psutil.Error:
            return
        for p in kids:
            try:
                p.kill()
            except psutil.Error:
                pass

    def run(self) -> None:
        while not self._stop.is_set():
            rss = self._tree_rss_mb()
            if rss > self.peak_mb:
                self.peak_mb = rss
            if rss > self.cap_mb:
                self.tripped = True
                print(
                    f"\n[WATCHDOG] tree RSS {rss:.0f} MB > cap {self.cap_mb} MB "
                    f"-- killing descendants to protect the OS.",
                    flush=True,
                )
                self._kill_descendants()
                return
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()


# --- Worker (module-level: picklable under spawn) -----------------------------
def _decode_huge(args) -> tuple[int, float, float]:
    """Decode + re-encode a synthetic huge image; report worker peak RSS.

    Mirrors what a real calibration worker does to a 56MP frame: full pixel
    decode plus an encode pass that buffers the frame. Returns
    (index, peak_rss_mb, elapsed_s).
    """
    idx, width, height = args
    from PIL import Image

    proc = psutil.Process()
    t0 = time.perf_counter()

    # Build the frame in memory (full decode equivalent), then encode it.
    img = Image.new("RGB", (width, height), (idx * 7 % 256, 64, 192))
    buf = io.BytesIO()
    img.save(buf, "PNG")          # forces a full-frame encode pass
    buf.seek(0)
    Image.open(buf).load()        # forces a full-frame decode pass

    peak = proc.memory_info().rss / (1024 * 1024)
    return idx, peak, time.perf_counter() - t0


# --- Probe 1: the guard gap, pure and instant ---------------------------------
def probe_guard_gap() -> bool:
    """Show _determine_concurrency fails to throttle on unprobed image dicts.

    Returns True if the bug is present (no throttle), False if already fixed.
    """
    print("\n=== Probe 1: concurrency-guard gap (pure, no processes) ===")
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # The engine imports with `core.*` as root; the runtime puts <repo>/app on
    # sys.path. Mirror that so the real calibration module resolves.
    for p in (os.path.join(repo_root, "app"), repo_root):
        if p not in sys.path:
            sys.path.insert(0, p)

    cpu = os.cpu_count() or 4

    # The 56MP image as it ACTUALLY arrives in the air-gap path: no dims yet.
    unprobed = [{"name": "huge_56mp.png"}]
    # The same image once dims are populated (what the guard expects).
    probed = [{"name": "huge_56mp.png", "width": 8000, "height": 7000}]

    # Prefer the REAL method. The engine package is WIP and its import chain may
    # be half-wired, so fall back to a faithful mirror of calibration.py:303-307.
    try:
        from core.engine.phases.calibration import CalibrationPhase
        from core.constraints import MASSIVE_IMAGE_THRESHOLD
        phase = CalibrationPhase.__new__(CalibrationPhase)  # skip __init__ wiring
        determine = phase._determine_concurrency
        source = "real CalibrationPhase._determine_concurrency"
    except Exception as e:
        print(f"  [note] real import unavailable ({e});")
        print("         mirroring calibration.py:303-307 verbatim instead.")
        try:
            from core.constraints import MASSIVE_IMAGE_THRESHOLD
        except Exception:
            MASSIVE_IMAGE_THRESHOLD = 50_000_000  # shipped value

        def determine(images):  # mirror of the source under test
            max_workers = (os.cpu_count() or 4) // 2
            has_massive = any(
                (img.get("width", 0) * img.get("height", 0)) > MASSIVE_IMAGE_THRESHOLD
                for img in images
            )
            if has_massive:
                max_workers = min(max_workers, 2)
            return max_workers
        source = "mirrored calibration.py:303-307"

    print(f"  source under test        : {source}")
    workers_unprobed = determine(unprobed)
    workers_probed = determine(probed)

    huge_px = 8000 * 7000
    print(f"  CPU cores                : {cpu}")
    print(f"  MASSIVE_IMAGE_THRESHOLD  : {MASSIVE_IMAGE_THRESHOLD:,} px")
    print(f"  56MP image area          : {huge_px:,} px "
          f"({'OVER' if huge_px > MASSIVE_IMAGE_THRESHOLD else 'under'} threshold)")
    print(f"  workers (dims MISSING)   : {workers_unprobed}   <-- air-gap reality")
    print(f"  workers (dims present)   : {workers_probed}   <-- guard's assumption")

    bug = workers_unprobed > 2
    if bug:
        print(f"  [BUG CONFIRMED] guard did NOT throttle: {workers_unprobed} "
              f"spawn workers each load a 56MP frame.")
    else:
        print("  [OK] guard throttled even without dims -- bug appears fixed.")
    return bug


# --- Probe 2: bounded, capped memory reproduction -----------------------------
def probe_memory(cap_mb: int, workers: int, huge_mp: int) -> None:
    print("\n=== Probe 2: spawn-pool memory under hard watchdog ===")
    side = int((huge_mp * 1_000_000) ** 0.5)
    width = height = side
    actual_mp = (width * height) / 1_000_000
    print(f"  image           : {width}x{height} (~{actual_mp:.1f} MP)")
    print(f"  workers         : {workers}  (buggy default = cpu_count//2 = {(os.cpu_count() or 4)//2})")
    print(f"  watchdog cap    : {cap_mb} MB (tree killed above this)")

    watchdog = TreeMemoryWatchdog(cap_mb)
    watchdog.start()

    tasks = [(i, width, height) for i in range(workers)]
    per_worker_peaks: list[float] = []
    t0 = time.perf_counter()
    try:
        ctx = __import__("multiprocessing").get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
            futs = [pool.submit(_decode_huge, t) for t in tasks]
            for f in as_completed(futs):
                if watchdog.tripped:
                    break
                try:
                    idx, peak, dt = f.result()
                    per_worker_peaks.append(peak)
                    print(f"  worker {idx}: peak {peak:.0f} MB in {dt:.2f}s")
                except Exception as e:
                    print(f"  worker raised: {e}")
    finally:
        watchdog.stop()
        watchdog.join(timeout=2)

    elapsed = time.perf_counter() - t0
    print(f"\n  tree peak RSS   : {watchdog.peak_mb:.0f} MB")
    if per_worker_peaks:
        avg = sum(per_worker_peaks) / len(per_worker_peaks)
        full_cpu = (os.cpu_count() or 4) // 2
        print(f"  per-worker avg  : {avg:.0f} MB")
        print(f"  projected @ cpu_count//2 ({full_cpu} workers): "
              f"~{avg * full_cpu:.0f} MB of frame buffers alone")
    print(f"  elapsed         : {elapsed:.2f}s")
    if watchdog.tripped:
        print(f"  [REPRODUCED] tree crossed {cap_mb} MB -- this is the OOM path. "
              f"Watchdog killed it; the OS was never starved.")
    else:
        print("  [SAFE] stayed under cap at this worker count.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cap-mb", type=int, default=DEFAULT_CAP_MB)
    ap.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    ap.add_argument("--huge-mp", type=int, default=DEFAULT_HUGE_MP)
    args = ap.parse_args()

    workers = args.workers if args.workers else max(2, (os.cpu_count() or 4) // 2)

    print("PixelPivot OOM steel thread")
    print("=" * 60)
    bug = probe_guard_gap()
    probe_memory(args.cap_mb, workers, args.huge_mp)

    print("\n" + "=" * 60)
    print("VERDICT")
    if bug:
        print("  Root cause active: _determine_concurrency does not throttle")
        print("  unprobed huge images. Fix: probe dims before concurrency is")
        print("  chosen, OR have _determine_concurrency probe/treat missing")
        print("  dims as potentially-massive (fail safe -> throttle to <=2).")
    else:
        print("  Guard already throttles; OOM likely from another path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
