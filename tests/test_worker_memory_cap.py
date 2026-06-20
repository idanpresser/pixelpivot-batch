"""Tests for memory-aware worker capping in the batch convert path.

Root cause of the system-wide OOM (see scripts/steelthread_oom.py): the base
batch path scales workers by CPU count only. The one-time RAM check samples
available memory BEFORE the pool launches, so with plenty of headroom at
check-time it spawns cpu_count * SCALING workers that then all decode large
frames at once -> RAM exhaustion AFTER the check. The batch already carries a
`dimensions` dict, so worker count must also be bounded by projected peak
memory = workers * largest-decoded-frame.
"""
from app.core.converters.base import memory_aware_worker_cap


def test_large_images_reduce_worker_count():
    """One 56MP image with modest free RAM must throttle far below base."""
    dims = {"huge.png": (8000, 7000)}  # 56 MP
    cap = memory_aware_worker_cap(
        base_workers=44, dimensions=dims, input_paths=["huge.png"],
        available_ram_mb=2000,
    )
    assert cap < 44, "must throttle when a huge frame would blow RAM"
    assert cap >= 1


def test_small_images_keep_full_concurrency():
    """Small frames leave the CPU-derived worker count untouched."""
    dims = {f"s{i}.jpg": (1000, 800) for i in range(50)}  # 0.8 MP each
    cap = memory_aware_worker_cap(
        base_workers=44, dimensions=dims, input_paths=list(dims),
        available_ram_mb=16000,
    )
    assert cap == 44


def test_missing_dimensions_fail_safe():
    """Unknown dims must not silently grant full concurrency.

    This is the exact gap that caused the OOM: missing dims -> guard blind.
    With dims absent we assume a conservative frame size and still cap.
    """
    cap = memory_aware_worker_cap(
        base_workers=44, dimensions={}, input_paths=["a.jpg", "b.jpg"],
        available_ram_mb=1500,
    )
    assert cap < 44


def test_never_returns_below_one():
    """Even with almost no RAM the pool must keep at least one worker."""
    dims = {"huge.png": (10000, 10000)}  # 100 MP
    cap = memory_aware_worker_cap(
        base_workers=44, dimensions=dims, input_paths=["huge.png"],
        available_ram_mb=50,
    )
    assert cap == 1


def test_cap_never_exceeds_base():
    """Plentiful RAM must not inflate workers above the CPU-derived base."""
    dims = {"s.jpg": (500, 500)}
    cap = memory_aware_worker_cap(
        base_workers=8, dimensions=dims, input_paths=["s.jpg"],
        available_ram_mb=64000,
    )
    assert cap == 8
