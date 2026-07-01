"""
Pure helpers for FFmpegConverter.convert_batch().

Kept separate from ffmpeg_converter.py so each piece can be unit-tested in
isolation, and to keep the converter file focused on orchestration.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections import defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple, Union

from ..logger import get_logger
from ..utils import probe_image_dimensions, quality_to_jxl_distance
from ..otel import span

log = get_logger(__name__)


def group_by_dimensions(
    paths: List[str],
    dimensions: Optional[Dict[str, Tuple[int, int]]] = None,
) -> Dict[Optional[Tuple[int, int]], List[str]]:
    """Bucket input paths by exact (width, height) and return deterministically ordered dict.

    Sub-groups are sorted by pixel count descending (largest image2 wins first).
    Paths within each sub-group are sorted alphabetically (deterministic indices,
    reproducible output). Unprobeable bucket (key None) is emitted last.

    Args:
        paths: List of input image paths.
        dimensions: Optional dict mapping input paths to (width, height) tuples.

    Returns:
        Dict mapping (width, height) tuples to lists of paths, ordered by
        descending pixel count, with None key (unprobeable images) last.
    """
    buckets: Dict[Optional[Tuple[int, int]], List[str]] = defaultdict(list)
    for path in paths:
        wh = None
        if dimensions and path in dimensions:
            wh = dimensions[path]
            if wh == (0, 0):
                wh = None

        if wh is None:
            try:
                wh = probe_image_dimensions(path)
            except Exception as e:
                log.debug("Dimension probe failed for %s (%s); routing to fallback.", path, e)
                buckets[None].append(path)
                continue
        buckets[wh].append(path)

    for key in buckets:
        buckets[key].sort()

    real_keys = sorted(
        (k for k in buckets if k is not None),
        key=lambda wh: -(wh[0] * wh[1]),
    )
    ordered: Dict[Optional[Tuple[int, int]], List[str]] = {k: buckets[k] for k in real_keys}
    if None in buckets:
        ordered[None] = buckets[None]
    return ordered


def stage_inputs_for_image2(
    paths: List[str],
    staging_dir_path: str,
    ext: str,
) -> Dict[int, str]:
    """Stage inputs as frame00001.<ext>, frame00002.<ext>, ... for image2 demuxer.

    Uses os.link (hardlink) for zero-cost staging on the same volume; falls back
    to shutil.copy2 on cross-volume, FAT32, or permission errors.

    Args:
        paths: List of input image paths.
        staging_dir_path: Temp directory where staged files are written.
        ext: File extension (e.g., 'png', 'jpg') for the staged filenames.

    Returns:
        Dict mapping 1-based staged frame index to original filename stem
        (e.g., {1: 'image1', 2: 'image2', ...}). Used to reconstruct original
        filenames from ffmpeg's outX.<fmt> outputs.

    Raises:
        OSError: If both hardlinking and copying fail.
    """
    with span("staging"):
        rename_map: Dict[int, str] = {}
        for idx, src in enumerate(paths, start=1):
            staged = os.path.join(staging_dir_path, f"frame{idx:05d}.{ext}")
            try:
                os.link(src, staged)
            except OSError as link_err:
                log.debug("Hardlink failed for %s -> %s (%s); copying instead.", src, staged, link_err)
                try:
                    shutil.copy2(src, staged)
                except OSError as copy_err:
                    log.error(
                        "Staging failed for %s -> %s: hardlink raised %s; copy raised %s",
                        src, staged, link_err, copy_err,
                    )
                    raise
            rename_map[idx] = Path(src).stem
        return rename_map


@contextmanager
def staging_dir(prefix: str = "ffbatch_") -> Iterator[str]:
    """Context manager: create temp directory, yield its path, remove on exit.

    Args:
        prefix: Prefix for the temp directory name.

    Yields:
        Path to a temporary directory. Cleaned up on exit (even on exception).
    """
    d = tempfile.mkdtemp(prefix=prefix)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def build_image2_args(
    staging_dir_path: str,
    input_ext: str,
    output_ext: str,
    count: int,
    encoder_params: List[str],
) -> List[str]:
    """Build ffmpeg argv for image2-demuxer batch invocation.

    Args:
        staging_dir_path: Directory containing frame00001.<input_ext>, ...
        input_ext: Input file extension.
        output_ext: Output format (e.g., 'webp', 'avif').
        count: Number of frames (used for -vframes and output count).
        encoder_params: Encoder-specific flags (e.g., -c:v libwebp -quality 80).

    Returns:
        ffmpeg argv list (without binary name). Outputs are written as
        out00001.<output_ext>, out00002.<output_ext>, ... in the same directory.
    """
    # ffmpeg's image2 pattern parser is finicky with backslashes on Windows;
    # always emit forward slashes for the in/out glob patterns.
    normalized = staging_dir_path.replace("\\", "/").rstrip("/")
    in_pattern = f"{normalized}/frame%05d.{input_ext}"
    out_pattern = f"{normalized}/out%05d.{output_ext}"
    return [
        "-y",
        "-hide_banner",
        "-nostats",
        "-f", "image2",
        "-start_number", "1",
        "-i", in_pattern,
        *encoder_params,
        "-vframes", str(count),
        "-f", "image2",
        out_pattern,
    ]


def pack_chunks(
    pairs: Sequence[Tuple[str, str]],
    max_files: int,
    max_cmdline_bytes: int,
    fixed_overhead: int = 256,
    per_pair_overhead: int = 0,
) -> List[List[Tuple[str, str]]]:
    """Split (input_path, output_path) pairs into chunks respecting file and byte limits.

    Chunks respect Windows's CreateProcess command-line limit (8191 chars) by
    approximating per-pair cost as len(input) + len(output) + per_pair_overhead + 20.

    Args:
        pairs: List of (input_path, output_path) tuples.
        max_files: Maximum files per chunk.
        max_cmdline_bytes: Target command-line byte limit per chunk.
        fixed_overhead: Bytes reserved for ffmpeg binary and global flags.
        per_pair_overhead: Bytes per file (e.g., for -map flags, encoder params).

    Returns:
        List of chunks, each a list of (input_path, output_path) tuples.
    """
    chunks: List[List[Tuple[str, str]]] = []
    current: List[Tuple[str, str]] = []
    current_bytes = fixed_overhead

    for in_path, out_path in pairs:
        # 20 bytes/pair buffer: each path is quoted (2) + space-separated, plus
        # the per-output map/flag tokens ffmpeg/mogrify emit. 8 underestimated
        # the real cmdline cost and let oversized chunks slip past the cap.
        pair_bytes = len(in_path) + len(out_path) + per_pair_overhead + 20

        would_exceed_files = len(current) >= max_files
        would_exceed_bytes = (current_bytes + pair_bytes) > max_cmdline_bytes

        if current and (would_exceed_files or would_exceed_bytes):
            chunks.append(current)
            current = []
            current_bytes = fixed_overhead

        current.append((in_path, out_path))
        current_bytes += pair_bytes

    if current:
        chunks.append(current)
    return chunks


def build_multimap_args(
    chunk: List[Tuple[str, str]],
    encoder_params: List[str],
) -> List[str]:
    """Build ffmpeg argv for multi-input/multi-output batch invocation.

    Layout: `-y -hide_banner -nostats -i in0 -i in1 ... -map 0:v <encoder_params>
    out0 -map 1:v <encoder_params> out1 ...`. Encoder params are repeated per output
    because ffmpeg's argument parser binds output options to the immediately
    following output.

    Args:
        chunk: List of (input_path, output_path) tuples for this batch.
        encoder_params: Encoder-specific flags (e.g., -c:v libwebp -quality 80).

    Returns:
        ffmpeg argv list (without binary name).
    """
    args: List[str] = ["-y", "-hide_banner", "-nostats"]

    for in_path, _ in chunk:
        args.extend(["-i", in_path])

    for idx, (_, out_path) in enumerate(chunk):
        args.extend(["-map", f"{idx}:v"])
        args.extend(["-map_metadata", str(idx)])
        args.extend(encoder_params)
        args.append(out_path)

    return args


_FORMAT_PARAM_BUILDERS = {
    "webp": lambda q: ["-c:v", "libwebp", "-quality", str(q)],
    "avif": lambda q: ["-c:v", "libaom-av1", "-crf", str(q), "-cpu-used", "4"],
    "jxl":  lambda q: ["-c:v", "libjxl", "-distance", str(quality_to_jxl_distance(q)), "-pix_fmt", "rgb24"],
}


def encoder_params_for(target_format: str, quality: Union[int, float]) -> Optional[List[str]]:
    """Return encoder-specific CLI arguments for the given format and quality.

    Single source of truth for ffmpeg encoder params. Returns None for
    unsupported formats so callers can route to per-file fallback.

    Args:
        target_format: Output format ('webp', 'avif', or 'jxl').
        quality: Format-native quality value (0-100 for all formats).

    Returns:
        List of encoder argv arguments, or None if format is unsupported.
    """
    builder = _FORMAT_PARAM_BUILDERS.get(target_format)
    if not builder:
        return None
    return list(builder(quality))


def all_same_resolution(
    paths: List[str],
) -> bool:
    """Check that all input paths have the same (width, height).

    Defense-in-depth re-probe before image2-demuxer path. Returns True only if
    all probes succeed and all results equal the first.

    Args:
        paths: List of input image paths.

    Returns:
        True if all paths have identical dimensions, False otherwise.
    """
    if len(paths) <= 1:
        return True

    try:
        first = probe_image_dimensions(paths[0])
    except Exception as e:
        log.debug("Equality re-check failed on first probe (%s): %s", paths[0], e)
        return False

    for p in paths[1:]:
        try:
            if probe_image_dimensions(p) != first:
                return False
        except Exception as e:
            log.debug("Equality re-check failed on %s: %s", p, e)
            return False
    return True
