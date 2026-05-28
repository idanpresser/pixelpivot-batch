"""
Fake ffmpeg shim for FFmpegProcess unit tests.

Usage:
    python -m tests.fixtures.fake_ffmpeg --mode <mode> [--progress-count N]
                                         [--exit-code N] [--stall-after N]

Modes:
    ok              Emit `--progress-count` progress blocks then exit 0.
    fatal           Print a fatal-marker line ("out of memory") to stderr and exit 1.
    stall           Emit one progress block, then sleep forever (cancel test).
    wall_hang       Sleep forever with NO progress (wall-timeout test).
    cancel_ignore   Ignore `q\\n` on stdin; sleep forever until terminate/kill.
    stderr_flood    Emit many KB of stderr then exit 0 (ring-buffer test).
    nonzero_exit    Emit progress=end then exit with --exit-code.

The shim never touches the filesystem and never imports ffmpeg / av.
"""

from __future__ import annotations

import argparse
import sys
import time


def emit_progress_block(frame: int, *, done: bool) -> None:
    sys.stdout.write(f"frame={frame}\n")
    sys.stdout.write("fps=10.0\n")
    sys.stdout.write(f"out_time_us={frame * 33333}\n")
    sys.stdout.write(f"total_size={frame * 1024}\n")
    sys.stdout.write("bitrate=8.0kbits/s\n")
    sys.stdout.write("speed=0.5x\n")
    sys.stdout.write(f"progress={'end' if done else 'continue'}\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--progress-count", type=int, default=2)
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--block-interval", type=float, default=0.05)
    parser.add_argument("--stderr-bytes", type=int, default=32 * 1024)
    args, _unknown = parser.parse_known_args()

    mode = args.mode

    if mode == "ok":
        for i in range(1, args.progress_count + 1):
            emit_progress_block(i, done=(i == args.progress_count))
            time.sleep(args.block_interval)
        return 0

    if mode == "fatal":
        sys.stderr.write("[libavcodec] Error: out of memory while encoding\n")
        sys.stderr.flush()
        return 1

    if mode == "stall":
        emit_progress_block(1, done=False)
        # No further progress; supervisor's stall_timeout should fire.
        while True:
            time.sleep(0.5)

    if mode == "wall_hang":
        # No progress at all; wall_timeout must catch this.
        while True:
            time.sleep(0.5)

    if mode == "cancel_ignore":
        # Pretend the q\n stdin signal does nothing; supervisor must escalate.
        # We close stdin reads explicitly so a write of "q\n" from the parent
        # does not block its flush — but we ignore the value.
        try:
            sys.stdin.close()
        except Exception:
            pass
        while True:
            time.sleep(0.5)

    if mode == "stderr_flood":
        chunk = "warning: deprecated pixel format used\n"
        written = 0
        while written < args.stderr_bytes:
            sys.stderr.write(chunk)
            written += len(chunk)
        sys.stderr.flush()
        emit_progress_block(1, done=True)
        return 0

    if mode == "nonzero_exit":
        emit_progress_block(1, done=True)
        sys.stderr.write(f"failed with exit {args.exit_code}\n")
        sys.stderr.flush()
        return args.exit_code

    sys.stderr.write(f"unknown mode: {mode}\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
