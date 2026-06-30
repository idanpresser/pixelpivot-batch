"""Global Configuration & Constants
==================================
Centralized magic numbers and engineering constants for the PixelPivot Batch Engine.

All tunable lists, tables, and constants live here so operators can audit and adjust
in one place. Module-level constants in feature code should import from this file
rather than re-declaring values.

Categories:
-----------
Runtime floor:       MIN_PYTHON_VERSION — minimum supported Python version.
Process management:  Timeouts, telemetry intervals, buffer sizes for monitoring.
FFmpeg wrapper:      Stall detection, cancellation, timeout, fatal error markers.
UI & Display:        Limits for dashboard result tables.
Hardware & Resource: Image size thresholds, memory guards, disk rechecks.
Database & Batching: Thread pools, batch chunking, busy lock retries.
Telemetry:           Batch sizes, queue timeouts, GPU failure limits.
Heuristics:          Table version, min samples for curve fitting, default quality
                     fallbacks per tool/format, valid native quality ranges.
Hot Folder:          Debounce and polling intervals for hot folder monitoring.
Calibration:         Tolerance, iteration limits, target SSIM for quality calibration.
FFmpeg batch:        Image2 demuxer thresholds, multi-input/output chunk sizing.
Magick batch:        Command-line length limits for mogrify.
"""

import os

from .paths import APP_ROOT


# ---------------------------------------------------------------------------
# Runtime floor
# ---------------------------------------------------------------------------
# Minimum supported Python (major, minor). MUST match pyproject.toml's
# `requires-python` AND the cp ABI tag of the wheels in vendor/wheels/ AND
# the embedded distro version referenced by scripts/sandbox_init.ps1. The
# lifespan guard in app/batch_api/main.py raises if the running interpreter
# is older -- it is cheaper to fail loudly at boot than to hit cryptic pip
# errors during an air-gap install.
MIN_PYTHON_VERSION: tuple[int, int] = (3, 14)


# ---------------------------------------------------------------------------
# Process Management
# ---------------------------------------------------------------------------
FFMPEG_TIMEOUT = 120                # Default wall-clock timeout (s) — used as fallback
TELEMETRY_INTERVAL = 0.25           # Seconds between resource polling (CPU/RAM/GPU)
MAX_LOG_BUFFER = 500                # Max lines kept in memory for the live monitor

SHUTDOWN_GRACE_S = float(os.getenv("PIXELPIVOT_SHUTDOWN_GRACE_S", "30"))
"""Wall-clock seconds to let the active matrix chunk drain on SIGTERM before
force-terminating surviving child processes."""

SUBPROCESS_TERMINATE_TIMEOUT_S = 5.0
"""Seconds to wait after terminate() before kill() on a surviving child."""



# ---------------------------------------------------------------------------
# FFmpeg subprocess wrapper (app/core/ffmpeg/)
# ---------------------------------------------------------------------------
FFMPEG_STALL_TIMEOUT = 30.0
"""Seconds without a -progress block before the supervisor cancels the process."""

FFMPEG_TIMEOUT_BY_FORMAT: dict[str, float] = {
    "webp": 60.0,
    "avif": 180.0,
    "jxl":  300.0,
}
"""Per-format wall-clock timeouts (s). Falls back to FFMPEG_TIMEOUT if format absent."""

FFMPEG_CANCEL_ESCALATION_S: tuple[float, float] = (2.0, 2.0)
"""Cancellation stages: (wait after `q\\n` before terminate, wait after terminate before kill)."""

FFMPEG_STDERR_TAIL_BYTES = 4096
"""Max bytes of stderr retained in the ring buffer for diagnostics."""

FFMPEG_FATAL_MARKERS: tuple[str, ...] = (
    "exceeds 8192",
    "exceeds 4096",
    "no capable devices",
    "cuda_error",
    "not enough memory",
    "out of memory",
    "dimension too large",
    "cannot load",
    "minimum required nvidia driver",
    "nvenc error",
    "nvenc: failed",
    "driver version",
    "cuda",
)
"""Substrings (case-insensitive) in stderr that mark an unrecoverable failure.
Matching one immediately trips the circuit breaker — no retry."""


# ---------------------------------------------------------------------------
# UI & Display
# ---------------------------------------------------------------------------
RESULT_LIMIT_DASHBOARD = 100        # Max rows shown in the results panel table


# ---------------------------------------------------------------------------
# Hardware & Resource Limits
# ---------------------------------------------------------------------------
MASSIVE_IMAGE_THRESHOLD = 50_000_000 # 50 MP (Hard stop)
HUGE_IMAGE_THRESHOLD = 25_000_000    # 25 MP (Thumbnails for metric calculation)
VRAM_SAFE_THRESHOLD = 15_000_000     # 15 MP (Force CPU for SSIM)

# Preflight resource guards
MIN_AVAILABLE_RAM_BYTES = 50 * 1024 * 1024
MIN_FREE_DISK_BYTES     = 50 * 1024 * 1024
DISK_RECHECK_EVERY_CELLS = 10   # 0 disables mid-run rechecks


# ---------------------------------------------------------------------------
# Database & Batching
# ---------------------------------------------------------------------------
PERIODIC_EXPORT_BATCH_SIZE = 50     # Conversions between auto-exports
SQLITE_BUSY_ATTEMPTS = 5            # Number of retries for busy locks
SQLITE_BUSY_BASE_DELAY_S = 0.1      # Base delay for exponential backoff

# Thread pool for concurrent encodes (if tool doesn't support native batching)
CONCURRENT_ENCODES_SCALING_FACTOR = float(os.getenv("PIXELPIVOT_CONCURRENT_ENCODES_SCALING_FACTOR", "2.0"))
CONCURRENT_ENCODES_MIN_RAM_MB = 200      # min available RAM to spawn a new worker
CONCURRENT_ENCODES_MAX_WORKERS = os.getenv("PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS")
if CONCURRENT_ENCODES_MAX_WORKERS is not None:
    CONCURRENT_ENCODES_MAX_WORKERS = int(CONCURRENT_ENCODES_MAX_WORKERS)

# Magick batch chunking — keep cmdline under Windows' 8191-char CreateProcess limit.
# At ~80 chars/path: 200 files * 80 = 16 KB headroom on Linux; on Windows tune lower.
MAGICK_MOGRIFY_CHUNK = 200

# Sharp daemon in-flight cap. Kernel send buffer is typically 64 KB; small JSON
# requests are ~200 bytes, so 64 files * 200 = 13 KB — well under the buffer.
SHARP_PIPELINE_CHUNK = 64


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
TELEMETRY_BATCH_SIZE = 20           # Flush every N samples
TELEMETRY_QUEUE_TIMEOUT = 2.0       # Max seconds to wait before periodic flush

# How often to re-discover the process tree (children/grandchildren). Sampling
# (CPU/RAM per PID) still happens every TELEMETRY_INTERVAL — only the recursive
# walk is throttled. 1 s is long enough to avoid syscall churn on stable trees
# (Sharp daemon, FFmpegProcess) yet short enough to catch new children quickly.
TELEMETRY_CHILDREN_REFRESH_S = 1.0


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------
HEURISTIC_TABLE_PATH = APP_ROOT / "core" / "heuristic_table.json"

# Semantic version stamped into a generated heuristic table and recorded on each
# batch run (batch_runs.heuristic_version). Bump when the table's data or schema
# changes so a run's provenance stays meaningful across regenerations.
# 2.0.0: schema changed from category->bucket->format->tool->value (4 bucket
# means + linear interpolation) to category->format->tool->{a,b,n,mp_min,mp_max}
# (a log-linear quality=f(MP) curve fitted over raw per-image samples).
HEURISTIC_TABLE_VERSION = "2.0.0"

# Minimum samples a (category, format, tool) curve must have for the generator to
# fit and emit it. Thinner cells are dropped so the interpolator falls back to a
# tool/format-native default rather than trusting a noisy handful of points.
HEURISTIC_MIN_SAMPLES = 5

# Fallback quality used when no heuristic data exists for a (category, format,
# tool) combo or when an image fails to probe. Each encoder expects its quality
# in a NATIVE scalar, so a single number is unsafe: ffmpeg's libaom-av1 avif
# path takes a -crf (0..63, lower = better), whereas everything else takes a
# 0..100 "higher is better" quality (jxl included — converters map 0..100 to a
# Butteraugli distance via quality_to_jxl_distance). A flat 80.0 becomes an
# out-of-range "-crf 80" for ffmpeg avif (worst quality), and a flat 1.0 for jxl
# maps to distance 9.9 (near-worst). Lookups resolve most-specific first.
DEFAULT_QUALITY_GENERIC = 80.0
"""0..100, higher = better. Used when no more specific default applies."""

DEFAULT_QUALITY_BY_TOOL_FORMAT: dict[tuple[str, str], float] = {
    ("ffmpeg", "avif"): 30.0,   # libaom-av1 -crf (0..63), lower = better
}

DEFAULT_QUALITY_BY_FORMAT: dict[str, float] = {
    "jxl": 90.0,   # 0..100 quality; quality_to_jxl_distance(90) -> distance 1.0
}


def default_quality_for(tool: str, target_format: str) -> float:
    """Resolve a tool/format-native fallback quality.

    Precedence: exact (tool, format) -> format-level -> generic 0..100 default.
    """
    fmt = target_format.lower()
    key = (tool, fmt)
    if key in DEFAULT_QUALITY_BY_TOOL_FORMAT:
        return DEFAULT_QUALITY_BY_TOOL_FORMAT[key]
    if fmt in DEFAULT_QUALITY_BY_FORMAT:
        return DEFAULT_QUALITY_BY_FORMAT[fmt]
    return DEFAULT_QUALITY_GENERIC


# Valid native scalar range per encoder, used to clamp a fitted-curve quality so
# extrapolation never emits an out-of-range value. Mirrors the scale split behind
# default_quality_for: ffmpeg's libaom-av1 avif path is a CRF (0..63); everything
# else is a 0..100 quality (jxl included -- the 0..100 quality is mapped to a
# Butteraugli distance downstream, so 0..100 is the right clamp here).
QUALITY_RANGE_GENERIC: tuple[float, float] = (0.0, 100.0)
QUALITY_RANGE_BY_TOOL_FORMAT: dict[tuple[str, str], tuple[float, float]] = {
    ("ffmpeg", "avif"): (0.0, 63.0),
}


def quality_range_for(tool: str, target_format: str) -> tuple[float, float]:
    """Resolve the (min, max) valid native quality range for (tool, format)."""
    fmt = target_format.lower()
    return QUALITY_RANGE_BY_TOOL_FORMAT.get((tool, fmt), QUALITY_RANGE_GENERIC)


# Search direction per (tool, format): "descending" means a LOWER native value
# is better quality (ffmpeg avif is a libaom CRF, 0..63, lower = better). All
# other paths are 0..100 "higher is better". Used by the calibration search.
QUALITY_DIRECTION_BY_TOOL_FORMAT: dict[tuple[str, str], str] = {
    ("ffmpeg", "avif"): "descending",
}


def quality_direction_for(tool: str, target_format: str) -> str:
    """Resolve the search direction for (tool, format).

    Returns "descending" when a lower native quality value yields better
    quality, else "ascending".
    """
    fmt = target_format.lower()
    return QUALITY_DIRECTION_BY_TOOL_FORMAT.get((tool, fmt), "ascending")



# ---------------------------------------------------------------------------
# Hot Folder
# ---------------------------------------------------------------------------
HOT_FOLDER_READINESS_TIMEOUT_MS = 5000
HOT_FOLDER_READINESS_CHECK_MS = 500
HOT_FOLDER_DEBOUNCE_MS = 5000
HOT_FOLDER_POLLING_INTERVAL_S = 10.0


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
# Calibration (iterative SSIM-targeted quality search) is disabled: quality is
# resolved exclusively via HeuristicInterpolator (config.default_quality_for
# fallback). The calibration_results table and BatchRepository calibration
# methods are kept intact but inert — gated by this flag, not deleted — so the
# feature can be re-enabled without a migration. Override with
# PIXELPIVOT_CALIBRATION_ENABLED=true.
CALIBRATION_ENABLED = os.getenv("PIXELPIVOT_CALIBRATION_ENABLED", "false").lower() in (
    "1",
    "true",
    "yes",
)
CALIBRATION_SSIM_TOLERANCE = 0.005
MAX_CALIBRATION_ITERS = 10
TARGET_SSIM = 0.98


# --- FFmpeg batch conversion (Task: ffmpeg-multi-image-batching) ---
# Minimum sub-group size to justify image2-demuxer staging overhead.
# Below this, the temp dir + hardlink + rename cost exceeds the savings.
IMAGE2_THRESHOLD = 3

# The image2 demuxer fast path was observed to be unreliable for AVIF/JXL on
# some libaom/heif builds, so by default it only runs for WebP. Flip this
# (or set PIXELPIVOT_IMAGE2_ALLOW_LOSSY=1) to opt the AVIF/JXL matrices into
# the same staging+hardlink path. Multimap remains the safety net on failure.
IMAGE2_ALLOW_LOSSY_FORMATS: bool = os.getenv(
    "PIXELPIVOT_IMAGE2_ALLOW_LOSSY", ""
).lower() in ("1", "true", "yes", "on")

# Maximum input files per multi-input/multi-output ffmpeg chunk.
# Memory grows roughly linearly with this — keep modest.
FFMPEG_BATCH_MAX_FILES = 20

# Maximum command-line byte length per chunk. Windows CreateProcess
# enforces ~8191 chars; leave headroom for env interpolation, ffmpeg
# binary path, and encoder argument boilerplate.
FFMPEG_BATCH_MAX_CMDLINE_BYTES = 7000


# --- Magick mogrify batch (Task: pack_chunks cross-converter) ---
# Maximum command-line byte length per mogrify invocation. Same Windows
# CreateProcess limit as the FFmpeg batch — kept as a separate constant
# so the two converters can be tuned independently if needed.
MAGICK_MOGRIFY_MAX_CMDLINE_BYTES = 7000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def ffmpeg_wall_timeout_for(target_format: str) -> float:
    """Resolve the wall-clock timeout for a given target format."""
    return FFMPEG_TIMEOUT_BY_FORMAT.get(target_format, float(FFMPEG_TIMEOUT))
