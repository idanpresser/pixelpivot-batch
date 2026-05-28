"""
FFmpegProcess — supervised subprocess wrapper for a single ffmpeg invocation.

Design contract:
- Main thread owns the supervisor loop. It is the only thread that calls
  proc.terminate() or proc.kill(). Reader threads only drain pipes.
- Two reader threads: stderr (4 KB ring buffer + severity classification)
  and stdout (parses `-progress pipe:1` into ProgressSample events).
- Cancellation escalates in three stages: stdin `q\\n` -> terminate -> kill.
- Telemetry attachment is the caller's job: read .pid after spawn(), attach
  the monitor, then call run() to block until exit.
"""

from __future__ import annotations

import collections
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from ..config import (
    FFMPEG_CANCEL_ESCALATION_S,
    FFMPEG_STALL_TIMEOUT,
    FFMPEG_STDERR_TAIL_BYTES,
)
from ..logger import get_logger
from ..utils import kill_process_tree
from .errors import classify_stderr_line
from .progress import ProgressParser, ProgressSample

log = get_logger(__name__)


@dataclass
class FFmpegResult:
    """Result of an FFmpeg invocation via FFmpegProcess.run().

    Attributes:
        success: True if return code is 0 and no fatal errors / cancellation occurred.
        return_code: Exit code from the ffmpeg process.
        duration_ms: Wall-clock duration in milliseconds.
        progress_samples: List of ProgressSample events parsed from -progress pipe:1.
        stderr_tail: Last N bytes of stderr (bounded by FFMPEG_STDERR_TAIL_BYTES).
        error: Descriptive error message if success is False, else None.
        fatal: True if a fatal marker was detected in stderr (unrecoverable error).
        cancelled: True if the process was cancelled via cancel().
        timed_out: True if wall_timeout or stall_timeout triggered cancellation.
        stall_timed_out: True if stall_timeout (vs. wall_timeout) was the cause.
    """
    success: bool
    return_code: int | None
    duration_ms: float
    progress_samples: list[ProgressSample] = field(default_factory=list)
    stderr_tail: str = ""
    error: str | None = None
    fatal: bool = False
    cancelled: bool = False
    timed_out: bool = False
    stall_timed_out: bool = False


class FFmpegProcess:
    """Supervised subprocess wrapper for a single FFmpeg invocation.

    Design contract:
    - Main thread owns the supervisor loop (only thread calling terminate/kill).
    - Two reader threads: stderr (4 KB ring buffer + severity classification),
      stdout (parses -progress pipe:1 into ProgressSample events).
    - Cancellation escalates in three stages: stdin 'q\\n' -> terminate -> kill.
    - Caller attaches telemetry monitor to pid after spawn(), before calling run().
    """

    def __init__(
        self,
        ffmpeg_path: str,
        args: list[str],
        *,
        wall_timeout_s: float,
        stall_timeout_s: float = FFMPEG_STALL_TIMEOUT,
        on_progress: Callable[[ProgressSample], None] | None = None,
        on_stderr_line: Callable[[str], None] | None = None,
    ) -> None:
        """Initialize FFmpegProcess.

        Args:
            ffmpeg_path: Path to the ffmpeg binary.
            args: Arguments to pass to ffmpeg (without the binary path).
            wall_timeout_s: Maximum wall-clock time in seconds.
            stall_timeout_s: Maximum time without progress (seconds).
            on_progress: Optional callback for each ProgressSample event.
            on_stderr_line: Optional callback for each stderr line.
        """
        self.ffmpeg_path = ffmpeg_path
        self.args = list(args)
        self.wall_timeout_s = wall_timeout_s
        self.stall_timeout_s = stall_timeout_s
        self._on_progress = on_progress
        self._on_stderr_line = on_stderr_line

        self._proc: subprocess.Popen[str] | None = None
        self._cancel_flag = threading.Event()
        self._cancel_reason: str | None = None

        self._samples: collections.deque[ProgressSample] = collections.deque(maxlen=1000)
        self._fatal = False
        self._fatal_line: str | None = None

        self._stderr_buf: collections.deque[str] = collections.deque()
        self._stderr_bytes = 0
        self._stderr_lock = threading.Lock()

        self._last_progress_ts = 0.0  # monotonic; 0 = never
        self._progress_ts_lock = threading.Lock()

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    def spawn(self) -> int:
        """Launch the ffmpeg subprocess and return its PID.

        Caller should attach a TelemetryMonitor to this PID before calling run().

        Returns:
            The process ID of the spawned ffmpeg process.

        Raises:
            RuntimeError: If already spawned.
        """
        if self._proc is not None:
            raise RuntimeError("FFmpegProcess already spawned")

        cmd = [self.ffmpeg_path, *self.args]
        log.debug("FFmpegProcess spawning: %s", " ".join(cmd[:6]) + " ...")

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            creationflags=creationflags,
        )
        return self._proc.pid

    def run(self) -> FFmpegResult:
        """Supervise the ffmpeg process to completion and return result.

        Spawns if not yet spawned. Launches reader threads for stderr and stdout,
        then enters the supervisor loop with timeout logic. Returns FFmpegResult
        with success status, duration, progress samples, and error details.

        Returns:
            FFmpegResult with full invocation details.
        """
        if self._proc is None:
            self.spawn()
        assert self._proc is not None

        start = time.monotonic()
        with self._progress_ts_lock:
            self._last_progress_ts = start

        stderr_thread = threading.Thread(
            target=self._read_stderr, name="ffmpeg-stderr", daemon=True
        )
        stdout_thread = threading.Thread(
            target=self._read_stdout, name="ffmpeg-stdout", daemon=True
        )
        stderr_thread.start()
        stdout_thread.start()

        return_code = self._supervise(start)

        # Unstick readers by closing pipes if the proc is gone but readers blocked.
        for stream in (self._proc.stdout, self._proc.stderr):
            try:
                if stream and not stream.closed:
                    stream.close()
            except Exception:
                pass
        stdout_thread.join(timeout=1.0)
        stderr_thread.join(timeout=1.0)

        duration_ms = (time.monotonic() - start) * 1000.0
        stderr_tail = self._snapshot_stderr_tail()
        cancelled = self._cancel_flag.is_set()
        timed_out = self._cancel_reason in ("wall_timeout", "stall_timeout")
        stall_timed_out = self._cancel_reason == "stall_timeout"

        success = (return_code == 0) and not cancelled and not self._fatal

        error: str | None = None
        if not success:
            if self._fatal_line:
                error = self._fatal_line.strip()
            elif cancelled and self._cancel_reason:
                error = f"cancelled: {self._cancel_reason}"
            elif return_code is not None and return_code != 0:
                # Last non-empty line of stderr is usually the most useful diagnostic.
                error = self._last_meaningful_stderr() or f"exit code {return_code}"
            else:
                error = "unknown failure"

            # Diagnostic enhancement: attach command line to error message
            cmd_str = " ".join(str(a) for p in [[self.ffmpeg_path], self.args] for a in p)
            error = f"{error} (Cmd: {cmd_str})"

        return FFmpegResult(
            success=success,
            return_code=return_code,
            duration_ms=duration_ms,
            progress_samples=list(self._samples),
            stderr_tail=stderr_tail,
            error=error,
            fatal=self._fatal,
            cancelled=cancelled,
            timed_out=timed_out,
            stall_timed_out=stall_timed_out,
        )

    def cancel(self, reason: str = "user_cancel") -> None:
        """Request graceful shutdown via stdin 'q\\n'.

        Threadsafe and idempotent. Escalates to terminate/kill if ffmpeg
        doesn't exit promptly.

        Args:
            reason: Reason for cancellation (logged for debugging).
        """
        if self._cancel_flag.is_set():
            return
        self._cancel_reason = reason
        self._cancel_flag.set()

    # ------------------------------------------------------------------ supervisor

    def _supervise(self, start: float) -> int | None:
        """Supervise process execution with timeout escalation.

        Monitors wall-clock and stall timeouts. On cancellation, sends 'q\\n'
        to stdin, then escalates to terminate/kill if needed.

        Args:
            start: Monotonic time when supervision began.

        Returns:
            Exit code of the process.
        """
        assert self._proc is not None
        proc = self._proc
        graceful_at: float | None = None
        terminate_at: float | None = None
        kill_at: float | None = None
        graceful_wait, terminate_wait = FFMPEG_CANCEL_ESCALATION_S

        while True:
            try:
                return_code = proc.wait(timeout=0.5)
                return return_code
            except subprocess.TimeoutExpired:
                pass

            now = time.monotonic()
            wall_elapsed = now - start
            with self._progress_ts_lock:
                stall_elapsed = now - self._last_progress_ts

            # Trigger conditions
            if not self._cancel_flag.is_set():
                if wall_elapsed >= self.wall_timeout_s:
                    self.cancel("wall_timeout")
                elif stall_elapsed >= self.stall_timeout_s:
                    self.cancel("stall_timeout")

            # Cancellation state machine
            if self._cancel_flag.is_set() and graceful_at is None:
                graceful_at = now
                terminate_at = now + graceful_wait
                self._send_quit()

            if terminate_at is not None and now >= terminate_at:
                if proc.poll() is None:
                    log.warning(
                        "FFmpegProcess force cleanup (kill_process_tree) after %.1fs (reason=%s)",
                        graceful_wait, self._cancel_reason,
                    )
                    kill_process_tree(proc.pid, timeout_s=terminate_wait)
                return proc.wait()

    def _send_quit(self) -> None:
        """Send 'q\\n' to ffmpeg stdin for graceful shutdown."""
        assert self._proc is not None
        stdin = self._proc.stdin
        if stdin is None or stdin.closed:
            return
        try:
            stdin.write("q\n")
            stdin.flush()
            # Do NOT close stdin here — closing can deliver SIGPIPE on Linux
            # if ffmpeg tries another read. Let finalize() close it.
        except (BrokenPipeError, ValueError, OSError) as e:
            log.debug("send_quit suppressed: %s", e)

    # ------------------------------------------------------------------ readers

    def _read_stderr(self) -> None:
        """Reader thread: drain stderr into ring buffer with severity classification."""
        assert self._proc is not None
        stream = self._proc.stderr
        if stream is None:
            return
        try:
            for raw in iter(stream.readline, ""):
                if not raw:
                    break
                self._absorb_stderr(raw)
        except Exception as e:
            log.debug("stderr reader exited: %s", e)

    def _read_stdout(self) -> None:
        """Reader thread: parse -progress pipe:1 into ProgressSample events."""
        assert self._proc is not None
        stream = self._proc.stdout
        if stream is None:
            return
        parser = ProgressParser()
        try:
            for raw in iter(stream.readline, ""):
                if not raw:
                    break
                sample = parser.feed_line(raw)
                if sample is None:
                    continue
                self._samples.append(sample)
                with self._progress_ts_lock:
                    self._last_progress_ts = time.monotonic()
                if self._on_progress:
                    try:
                        self._on_progress(sample)
                    except Exception as e:
                        log.debug("on_progress callback raised: %s", e)
        except Exception as e:
            log.debug("stdout reader exited: %s", e)

    def _absorb_stderr(self, line: str) -> None:
        """Classify and buffer a stderr line; detect fatal errors.

        Args:
            line: A single line of stderr output.
        """
        if classify_stderr_line(line) == "fatal":
            self._fatal = True
            if self._fatal_line is None:
                self._fatal_line = line
        if self._on_stderr_line:
            try:
                self._on_stderr_line(line)
            except Exception as e:
                log.debug("on_stderr_line callback raised: %s", e)
        with self._stderr_lock:
            self._stderr_buf.append(line)
            self._stderr_bytes += len(line)
            while self._stderr_bytes > FFMPEG_STDERR_TAIL_BYTES and self._stderr_buf:
                dropped = self._stderr_buf.popleft()
                self._stderr_bytes -= len(dropped)

    def _snapshot_stderr_tail(self) -> str:
        """Return the last N bytes of stderr (thread-safe).

        Returns:
            Stderr tail string.
        """
        with self._stderr_lock:
            return "".join(self._stderr_buf)

    def _last_meaningful_stderr(self) -> str | None:
        """Return the last non-empty stderr line (thread-safe).

        Returns:
            Last meaningful stderr line, or None if no lines found.
        """
        with self._stderr_lock:
            for line in reversed(self._stderr_buf):
                stripped = line.strip()
                if stripped:
                    return stripped
        return None
