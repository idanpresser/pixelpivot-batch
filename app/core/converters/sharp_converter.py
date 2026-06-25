"""Sharp converter via persistent Node.js daemon socket connection."""

import socket
import json
import subprocess
import threading
import sys
import time
import os
import atexit
import shutil
from pathlib import Path
from typing import Union, List, Dict, Any, Optional
from .base import BaseConverter
from ..telemetry import TelemetryMonitor
from ..logger import get_logger

log = get_logger(__name__)


class SharpConverter(BaseConverter):
    """Convert still images via Sharp (Node.js binding to libvips) over a persistent socket.

    Maintains a long-lived daemon process (sharp_daemon.js) listening on a dynamic port.
    Single-file and batch conversions are pipelined over the socket (send all requests,
    then read all responses). Daemon auto-restart on connection loss.
    """

    def __init__(self, port: int = 8765):
        """Initialize Sharp converter.

        Args:
            port: Initial port number (may change if already in use).
        """
        super().__init__()
        self.port = port
        self.host = "127.0.0.1"
        self.daemon_process = None
        # Persistent socket is thread-local: convert_batch fans out across a
        # ThreadPoolExecutor, and a socket is a single bidirectional stream —
        # sharing one across threads interleaves requests/responses and corrupts
        # the framing. Each worker thread gets its own connection.
        self._local = threading.local()

    @property
    def _socket(self):
        return getattr(self._local, "socket", None)

    @_socket.setter
    def _socket(self, value):
        self._local.socket = value

    def get_name(self) -> str:
        """Return the converter name."""
        return "sharp"

    def supported_formats(self) -> list:
        """Return list of supported output formats."""
        return ["webp", "avif", "jxl", "jpeg", "png"]

    def _is_port_open(self, timeout: float = 0.5) -> bool:
        """Check if the daemon is listening on this port.

        Args:
            timeout: Connection timeout in seconds.

        Returns:
            True if a connection can be made, False otherwise.
        """
        try:
            with socket.create_connection((self.host, self.port), timeout=timeout):
                return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False

    def _close_socket(self):
        """Close the persistent socket connection."""
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    def _get_connection(self) -> socket.socket:
        """Return the persistent socket connection, creating it if necessary.

        Returns:
            A connected socket.socket instance.

        Raises:
            Exception: If connection fails.
        """
        if self._socket:
            return self._socket

        # Try to establish a new connection
        try:
            self._socket = socket.create_connection((self.host, self.port), timeout=30)
            # Set a long timeout for the persistent connection
            self._socket.settimeout(30.0)
            return self._socket
        except Exception as e:
            log.debug(f"Failed to create persistent Sharp connection: {e}")
            self._socket = None
            raise

    def _test_daemon_ready(self) -> bool:
        """Test daemon readiness by sending a ping request.

        Returns:
            True if daemon responds to ping, False otherwise.
        """
        try:
            # We use a one-off connection for readiness check to avoid polluting
            # the persistent socket state during startup/restart.
            with socket.create_connection((self.host, self.port), timeout=2.0) as sock:
                # Send a minimal ping request
                ping = {
                    "inputPath": "",
                    "outputPath": "",
                    "format": "png",
                    "quality": 50,
                    "ping": True,
                }
                sock.sendall((json.dumps(ping) + "\n").encode("utf-8"))
                sock.settimeout(2.0)
                response = sock.recv(4096).decode("utf-8").strip()
                result = json.loads(response)
                return result.get("success", False) or result.get("pong", False)
        except Exception:
            return False

    def _stop_daemon(self):
        """Stop the Node.js sharp daemon and close its socket."""
        self._close_socket()
        if self.daemon_process and self.daemon_process.poll() is None:
            if not sys.is_finalizing():
                try:
                    log.info("Stopping Sharp daemon...")
                except Exception:
                    pass
            try:
                self.daemon_process.terminate()
                self.daemon_process.wait(timeout=2)
            except Exception:
                self.daemon_process.kill()
            self.daemon_process = None

    def _ensure_daemon_running(self):
        """Start the Node.js sharp daemon if not already running.

        Finds the daemon script (app/scripts/sharp_daemon.js), spawns it with a
        dynamic port allocation, and polls for readiness. Retries on transient
        startup failures. Registers an atexit cleanup hook.

        Raises:
            RuntimeError: If Node.js is not available or daemon fails to start
                after max retries.
        """
        if self._is_port_open() and self._test_daemon_ready():
            return  # already up and responsive

        # Resolve daemon path: app/scripts/sharp_daemon.js
        # __file__ is in app/core/converters/, so we need to go up 2 levels to 'app'
        app_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        daemon_path = os.path.join(app_dir, "scripts", "sharp_daemon.js")

        # Check for portable Node.js or system Node.js
        root_dir = os.path.dirname(app_dir)  # Go up one more level to project root
        portable_node = (
            os.path.join(root_dir, "node", "node.exe")
            if sys.platform == "win32"
            else os.path.join(root_dir, "node", "node")
        )
        node_cmd = None
        if os.path.exists(portable_node):
            node_cmd = portable_node
        else:
            node_cmd = shutil.which("node") or shutil.which("nodejs")

        if not node_cmd:
            raise RuntimeError(
                "Sharp daemon requires Node.js. "
                "Install Node.js in the container or run scripts/setup_sharp_portable.ps1 to install portable Node.js."
            )

        # Find a free port dynamically to avoid TIME_WAIT / EADDRINUSE issues on restart
        # Hardened with retry loop to minimize TOCTOU race
        max_spawn_retries = 3
        configured_port = self.port
        for spawn_attempt in range(max_spawn_retries):
            target_port = configured_port if spawn_attempt == 0 else 0
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(("", target_port))
                    self.port = s.getsockname()[1]
            except OSError as bind_err:
                log.debug(f"Could not bind to port {target_port}: {bind_err}")
                if target_port == 0:
                    raise
                continue

            # Run daemon with cwd=project root so require('sharp') resolves to ./node_modules
            log.info(f"Starting Sharp daemon on port {self.port} (attempt {spawn_attempt+1})...")
            # Use CREATE_NO_WINDOW on Windows to prevent console flash
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            self.daemon_process = subprocess.Popen(
                [node_cmd, daemon_path, str(self.port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.PIPE,  # Keep pipe open so Node dies when Python dies
                text=True,
                cwd=root_dir,
                creationflags=creationflags,
            )

            # Fix #9: register shutdown hook via atexit (reliable vs. __del__)
            atexit.register(self._stop_daemon)

            # Fix #10: Poll the port AND test readiness instead of fixed sleep
            deadline = time.monotonic() + 10.0  # Increased timeout to 10 seconds
            success = False
            while time.monotonic() < deadline:
                if self.daemon_process.poll() is not None:
                    # Process exited; capture stderr for diagnostics
                    try:
                        _, stderr_out = self.daemon_process.communicate(timeout=1)
                    except Exception:
                        stderr_out = "(no stderr)"
                    err_msg = (stderr_out or "").strip() or "(no stderr)"
                    log.warning(f"Sharp daemon attempt {spawn_attempt+1} failed to start. stderr: {err_msg}")
                    break # Try next port

                # Test both port AND readiness
                if self._is_port_open(timeout=0.3) and self._test_daemon_ready():
                    log.info(f"Sharp daemon ready on port {self.port}")
                    return
                time.sleep(0.2)

            # If we broke out of while but daemon is still alive, it's a timeout
            if self.daemon_process.poll() is None:
                log.warning(f"Sharp daemon attempt {spawn_attempt+1} timed out.")
                self._stop_daemon()
            else:
                self.daemon_process = None

        # If all retries exhausted
        raise RuntimeError(
            f"Sharp daemon failed to start after {max_spawn_retries} attempts. "
            "Ensure Node.js is available and 'npm install' has been run."
        )

    def convert(
        self,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
        is_intermediate: bool = False,
        run_id: Optional[int] = None,
    ) -> dict:
        """Convert a single image via Sharp daemon socket.

        Sends a JSON request to the daemon, with socket retry logic on transient
        failures (ConnectionRefusedError, timeout). Restarts the daemon on
        persistent failure.

        Args:
            input_path: Path to input image.
            output_path: Path where output should be written.
            target_format: Output format ('webp', 'avif', 'jxl', 'jpeg', 'png').
            quality: Quality value (0-100 for most formats; float for JXL distance).
            is_intermediate: Unused.
            run_id: Optional batch run ID for telemetry.

        Returns:
            Dict with conversion result including success status, duration, telemetry,
            and error details.
        """
        self._set_active_run_id(run_id)
        self._ensure_daemon_running()

        start_total = time.perf_counter()
        monitor = None
        if self.daemon_process:
            monitor = TelemetryMonitor(pid=self.daemon_process.pid, interval_ms=50, run_id=run_id)
            monitor.start()

        # Retry logic for socket connections (handles transient timeouts)
        max_retries = 2
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                sock = self._get_connection()
                # Fix: Preserve float distance values for JXL; otherwise round to the
                # nearest int (unbiased) at this final encoder boundary, not truncate.
                val_quality = float(quality) if target_format == "jxl" else round(quality)
                request = {
                    "inputPath": input_path,
                    "outputPath": output_path,
                    "format": target_format,
                    "quality": val_quality,
                }
                sock.sendall((json.dumps(request) + "\n").encode("utf-8"))

                # Wait for response with timeout
                response_data = b""
                try:
                    while len(response_data) < 10000:  # Reasonable limit
                        chunk = sock.recv(4096)
                        if not chunk:
                            # Connection closed by peer
                            self._socket = None
                            break
                        response_data += chunk
                        if response_data.endswith(b"\n"):
                            break
                except socket.timeout:
                    log.error("Sharp daemon response timeout. Failing conversion.")
                    if monitor:
                        monitor.stop()
                    self._mark_failure()
                    return {"success": False, "error": "Sharp daemon timed out"}

                # If we got a response, parse it
                if response_data.strip():
                    try:
                        result = json.loads(response_data.decode("utf-8").strip())
                    except Exception as e:
                        log.error(f"Failed to parse Sharp daemon JSON response: {e}")
                        result = {"success": False, "error": "Malformed JSON response from daemon"}
                else:
                    # No response received, check if file exists as a last resort but log a warning
                    log.warning("Sharp daemon returned empty response. Checking file existence...")
                    file_exists = os.path.exists(output_path)
                    result = {
                        "success": file_exists,
                        "error": None if file_exists else "No response from daemon and output file missing",
                        "duration_ms": 1000,
                    }

                duration_total = (time.perf_counter() - start_total) * 1000

                # Capture telemetry from the daemon process (#7)
                telemetry = monitor.stop() if monitor else {}
                monitor = None  # Telemetry stopped successfully, prevent double-stop in finally/exhaustion

                if result.get("success"):
                    self._reset_failures()
                    log.debug(f"Sharp success: Q={quality}, format={target_format}")
                    return {
                        "success": True,
                        "duration_ms": result.get("duration_ms", 1000),
                        "total_overhead_ms": duration_total
                        - result.get("duration_ms", 1000),
                        "parameters_used": {
                            "quality": val_quality,
                            "format": target_format,
                        },
                        "telemetry": telemetry,
                        "error": None,
                    }
                else:
                    self._mark_failure()
                    error_msg = result.get("error") or "Unknown Sharp daemon error"
                    log.error(f"Sharp daemon error: {error_msg}")
                    return {"success": False, "error": error_msg}

            except OSError as e:
                # OSError covers socket.timeout, all ConnectionError subclasses
                # (reset/refused/aborted), BrokenPipeError, and bare OSError —
                # every transient socket fault is retryable here.
                last_error = e
                log.warning(
                    f"Sharp socket error (attempt {attempt + 1}/{max_retries + 1}): {e}"
                )
                self._close_socket()  # Close the broken persistent connection

                # Ensure the previous monitor is stopped before restarting/retrying
                if monitor:
                    monitor.stop()
                    monitor = None

                if attempt < max_retries:
                    # Stop daemon and restart fresh
                    self._stop_daemon()
                    self._ensure_daemon_running()
                    if self.daemon_process:
                        monitor = TelemetryMonitor(
                            pid=self.daemon_process.pid, interval_ms=50, run_id=run_id
                        )
                        monitor.start()
                    time.sleep(0.5)  # Brief pause before retry
                continue
            except Exception as e:
                last_error = e
                self._mark_failure()
                log.error(f"Sharp socket error: {e}")
                break

        # All retries exhausted. The daemon was already stopped/restarted between
        # attempts; trip the circuit breaker and surface the attempt count.
        if monitor:
            monitor.stop()
        self._mark_failure()
        return {
            "success": False,
            "error": f"Sharp conversion failed after {max_retries + 1} attempts: {last_error}",
        }

    def convert_batch(
        self,
        input_paths: List[str],
        output_dir: str,
        target_format: str,
        qualities: List[float],
        run_id: Optional[int] = None,
        suffix: str = "",
        dimensions: Optional[Dict[str, tuple[int, int]]] = None,
    ) -> Dict[str, Any]:
        """Convert a batch of images via pipelined Sharp daemon socket.

        Sends all JSON requests in a pipeline, then reads all responses sequentially.
        Falls back to per-file convert() if pipelining fails.

        Args:
            input_paths: List of input file paths.
            output_dir: Directory where output files are written.
            target_format: Output format.
            qualities: Per-file quality values.
            run_id: Optional batch run ID for telemetry.
            suffix: Optional filename suffix.
            dimensions: Optional pre-computed dimensions (unused).

        Returns:
            Dict with 'success_count', 'failure_count', 'duration_ms', 'telemetry',
            and 'errors' keys.
        """
        self._ensure_daemon_running()
        start = time.time()
        success_count = 0
        failure_count = 0
        errors = []

        os.makedirs(output_dir, exist_ok=True)

        # FIX #6: Capture telemetry for batch
        monitor = None
        if self.daemon_process:
            monitor = TelemetryMonitor(pid=self.daemon_process.pid, interval_ms=100, run_id=run_id)
            monitor.start()

        try:
            sock = self._get_connection()
            # 1. Send all requests (pipelining)
            for in_path, q in zip(input_paths, qualities):
                filename = Path(in_path).stem
                out_path = str(Path(output_dir) / f"{filename}{suffix}.{target_format}")
                # Fix: Preserve float distance values for JXL, otherwise cast to int
                val_quality = float(q) if target_format == "jxl" else int(q)
                request = {
                    "inputPath": in_path,
                    "outputPath": out_path,
                    "format": target_format,
                    "quality": val_quality,
                }
                sock.sendall((json.dumps(request) + "\n").encode("utf-8"))

            # 2. Read all responses
            response_buffer = b""
            expected_responses = len(input_paths)
            received_count = 0

            # Set a generous timeout for the whole batch
            sock.settimeout(60.0 + (len(input_paths) * 0.5))

            while received_count < expected_responses:
                chunk = sock.recv(16384)
                if not chunk:
                    break
                response_buffer += chunk
                while b"\n" in response_buffer:
                    line, response_buffer = response_buffer.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        result = json.loads(line.decode("utf-8"))
                        if result.get("success"):
                            success_count += 1
                        else:
                            failure_count += 1
                            errors.append({"path": None, "error": result.get("error") or "Unknown daemon error"})
                    except Exception as e:
                        failure_count += 1
                        errors.append({"path": None, "error": f"Response parse error: {e}"})
                    received_count += 1

            self._account_native_batch(failed=failure_count > 0)

        except Exception as e:
            log.error(f"Sharp batch conversion failed: {e}")
            self._close_socket()
            if monitor:
                monitor.stop()
                monitor = None
            # Fallback to default (individual) if pipelining failed
            return self._default_batch_convert(
                input_paths, output_dir, target_format, qualities, run_id=run_id, suffix=suffix, dimensions=dimensions
            )

        telemetry = monitor.stop() if monitor else {}

        return {
            "success_count": success_count,
            "failure_count": failure_count,
            "duration_ms": (time.time() - start) * 1000,
            "telemetry": telemetry,
            "errors": errors,
        }
