"""
Unit tests for FFmpegProcess against the fake-ffmpeg shim.

These tests never invoke real ffmpeg. The shim lives at
tests/fixtures/fake_ffmpeg/__main__.py and is invoked via
`sys.executable -m tests.fixtures.fake_ffmpeg`.
"""

from __future__ import annotations

import sys
import time

import pytest

from app.core.ffmpeg import FFmpegProcess


def _shim_cmd(mode: str, **kw) -> tuple[str, list[str]]:
    """Returns (executable, args) where args = ['-m', module, '--mode', ...]."""
    args = ["-m", "tests.fixtures.fake_ffmpeg", "--mode", mode]
    for k, v in kw.items():
        args.append(f"--{k.replace('_', '-')}")
        args.append(str(v))
    return sys.executable, args


def test_normal_exit_emits_progress_samples():
    exe, args = _shim_cmd("ok", progress_count=3)
    proc = FFmpegProcess(exe, args, wall_timeout_s=10.0, stall_timeout_s=5.0)
    result = proc.run()

    assert result.success is True
    assert result.return_code == 0
    assert len(result.progress_samples) == 3
    assert result.progress_samples[-1].done is True
    assert result.fatal is False
    assert result.cancelled is False
    assert result.timed_out is False


def test_progress_callback_is_invoked():
    exe, args = _shim_cmd("ok", progress_count=2)
    received = []
    proc = FFmpegProcess(
        exe, args,
        wall_timeout_s=10.0,
        stall_timeout_s=5.0,
        on_progress=received.append,
    )
    result = proc.run()
    assert result.success is True
    assert len(received) == 2


def test_fatal_marker_in_stderr_sets_flag():
    exe, args = _shim_cmd("fatal")
    proc = FFmpegProcess(exe, args, wall_timeout_s=10.0, stall_timeout_s=5.0)
    result = proc.run()

    assert result.success is False
    assert result.fatal is True
    assert result.error is not None
    assert "out of memory" in result.error.lower()


def test_stall_timeout_kills_silent_process():
    exe, args = _shim_cmd("stall")
    proc = FFmpegProcess(exe, args, wall_timeout_s=30.0, stall_timeout_s=1.0)
    start = time.monotonic()
    result = proc.run()
    elapsed = time.monotonic() - start

    assert result.success is False
    assert result.cancelled is True
    assert result.timed_out is True
    assert result.stall_timed_out is True
    # 1s stall + up to 4s escalation budget; 8s gives ample slack on CI.
    assert elapsed < 8.0, f"stall took too long: {elapsed:.1f}s"


def test_wall_timeout_kills_no_progress_process():
    exe, args = _shim_cmd("wall_hang")
    proc = FFmpegProcess(exe, args, wall_timeout_s=1.0, stall_timeout_s=30.0)
    start = time.monotonic()
    result = proc.run()
    elapsed = time.monotonic() - start

    assert result.success is False
    assert result.timed_out is True
    assert result.stall_timed_out is False
    assert elapsed < 8.0


def test_cancel_escalates_to_kill_when_quit_ignored():
    exe, args = _shim_cmd("cancel_ignore")
    proc = FFmpegProcess(exe, args, wall_timeout_s=30.0, stall_timeout_s=30.0)

    import threading
    def fire_cancel():
        time.sleep(0.3)
        proc.cancel("test")

    t = threading.Thread(target=fire_cancel, daemon=True)
    t.start()

    start = time.monotonic()
    result = proc.run()
    elapsed = time.monotonic() - start

    assert result.cancelled is True
    assert result.success is False
    # cancel @ 0.3s + 2s graceful + 2s after terminate -> ~4.3s upper bound; allow CI slack
    assert elapsed < 8.0


def test_stderr_ring_buffer_is_bounded():
    exe, args = _shim_cmd("stderr_flood", stderr_bytes=64 * 1024)
    proc = FFmpegProcess(exe, args, wall_timeout_s=10.0, stall_timeout_s=5.0)
    result = proc.run()

    assert result.success is True
    # FFMPEG_STDERR_TAIL_BYTES default is 4096; allow up to one full line of slack.
    assert len(result.stderr_tail.encode("utf-8")) <= 4096 + 64


def test_nonzero_exit_records_error_message():
    exe, args = _shim_cmd("nonzero_exit", exit_code=42)
    proc = FFmpegProcess(exe, args, wall_timeout_s=10.0, stall_timeout_s=5.0)
    result = proc.run()

    assert result.success is False
    assert result.return_code == 42
    assert result.error is not None
    assert "42" in result.error or "failed" in result.error.lower()


def test_pid_available_after_spawn_before_run():
    """The orchestrator needs proc.pid before run() so it can attach telemetry."""
    exe, args = _shim_cmd("ok", progress_count=1)
    proc = FFmpegProcess(exe, args, wall_timeout_s=10.0, stall_timeout_s=5.0)
    pid = proc.spawn()
    assert isinstance(pid, int)
    assert pid > 0
    assert proc.pid == pid
    result = proc.run()
    assert result.success is True


def test_cancel_before_run_starts_is_idempotent():
    exe, args = _shim_cmd("ok", progress_count=1)
    proc = FFmpegProcess(exe, args, wall_timeout_s=10.0, stall_timeout_s=5.0)
    proc.cancel("first")
    proc.cancel("second")  # second call must not raise or overwrite reason
    result = proc.run()
    # We cancelled before spawn; result.cancelled True, success False
    assert result.cancelled is True
    assert result.success is False
