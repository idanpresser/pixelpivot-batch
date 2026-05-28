"""GPU Utilities — NVIDIA GPU detection, CUDA availability, and NVENC capability checks.

Single source of truth for GPU detection and device resolution.

Detection uses pynvml (the NVIDIA Management Library, same as telemetry.py) as
the primary probe because it works from the GPU driver alone — independent of
whether torch was built with CUDA support or whether the CUDA runtime DLLs are
on PATH at startup.

torch.cuda.is_available() is only used in get_device(), where it actually
matters for compute: it guards the "cuda" string so PyTorch never receives a
device it can't use.
"""

from .logger import get_logger

log = get_logger(__name__)

_GPU_INFO: dict = None  # cached after first call to detect_gpu()


def detect_gpu() -> dict:
    """
    Probes the NVIDIA driver via pynvml and (optionally) torch for CUDA version.

    Returns:
        {
            "available":      bool,   # True if an NVIDIA GPU is present and accessible
            "name":           str,    # e.g. "NVIDIA GeForce RTX 3080" or "CPU only"
            "vram_gb":        float,  # Total VRAM in GB (0.0 if no GPU)
            "cuda_version":   str,    # e.g. "12.1" (from torch) or ""
            "driver_version": str,    # NVIDIA driver version string or ""
        }
    """
    global _GPU_INFO
    if _GPU_INFO is not None:
        return _GPU_INFO

    info = {
        "available": False,
        "name": "CPU only",
        "vram_gb": 0.0,
        "cuda_version": "",
        "driver_version": "",
    }

    # --- Primary probe: pynvml (driver-level, no CUDA runtime needed) -----------
    try:
        from pynvml import (
            nvmlInit,
            nvmlDeviceGetHandleByIndex,
            nvmlDeviceGetName,
            nvmlDeviceGetMemoryInfo,
            nvmlSystemGetDriverVersion,
        )

        nvmlInit()
        handle = nvmlDeviceGetHandleByIndex(0)

        raw_name = nvmlDeviceGetName(handle)
        # pynvml returns bytes on older versions, str on newer
        info["name"] = raw_name.decode() if isinstance(raw_name, bytes) else raw_name

        mem = nvmlDeviceGetMemoryInfo(handle)
        info["vram_gb"] = round(mem.total / (1024**3), 1)

        info["driver_version"] = nvmlSystemGetDriverVersion()
        info["available"] = True

    except Exception as e:
        log.debug(f"pynvml GPU probe failed: {e}")
        _GPU_INFO = info
        return _GPU_INFO

    # --- Secondary probe: torch (optional — adds CUDA version string) -----------
    try:
        import torch

        if torch.version.cuda:
            info["cuda_version"] = torch.version.cuda
    except Exception:
        pass  # torch not installed or no CUDA build — fine, pynvml already succeeded

    log.info(
        f"GPU detected: {info['name']} | {info['vram_gb']} GB VRAM | "
        f"CUDA {info['cuda_version'] or 'n/a'} | driver {info['driver_version']}"
    )
    _GPU_INFO = info
    return _GPU_INFO


def has_av1_nvenc(ffmpeg_path: str) -> bool:
    """
    Returns True when BOTH conditions are met:
      1. The GPU is Ada Lovelace (RTX 4000+) or newer — compute capability >= 8.9
      2. The bundled FFmpeg was compiled with the av1_nvenc encoder

    Either condition alone is not sufficient: av1_nvenc only exists on Ada+ silicon,
    and the FFmpeg binary must have been built with NVENC SDK support.
    """
    # --- 1. Compute capability check -----------------------------------------
    compute_ok = False

    # Preferred path: torch (exact, numeric)
    try:
        import torch

        if torch.cuda.is_available():
            major, minor = torch.cuda.get_device_capability(0)
            compute_ok = (major, minor) >= (8, 9)
    except Exception:
        pass

    # Fallback: pynvml exposes nvmlDeviceGetCudaComputeCapability
    if not compute_ok:
        try:
            from pynvml import (
                nvmlInit,
                nvmlDeviceGetHandleByIndex,
                nvmlDeviceGetCudaComputeCapability,
            )

            nvmlInit()
            handle = nvmlDeviceGetHandleByIndex(0)
            major, minor = nvmlDeviceGetCudaComputeCapability(handle)
            compute_ok = (major, minor) >= (8, 9)
        except Exception:
            pass

    # Last resort: GPU name heuristic (RTX 40xx / 50xx / 60xx / H100 / B100 etc.)
    if not compute_ok:
        info = detect_gpu()
        name = info.get("name", "").upper()
        # Ada Lovelace: RTX 4xxx consumer, RTX 4000/5000/6000 Ada workstation
        # Blackwell: RTX 5xxx consumer, GB series
        # H/B datacenter parts also support AV1 NVENC
        ADA_OR_NEWER = (
            "RTX 40",
            "RTX 50",
            "RTX 60",
            "RTX 4000 ADA",
            "RTX 5000 ADA",
            "RTX 6000 ADA",
            "H100",
            "H200",
            "B100",
            "B200",
            "GH200",
            "GB200",
        )
        compute_ok = any(pat in name for pat in ADA_OR_NEWER)

    if not compute_ok:
        log.debug("av1_nvenc: GPU compute capability < 8.9 — skipping")
        return False

    # --- 2. FFmpeg encoder availability check ---------------------------------
    try:
        import subprocess
        import sys

        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=creationflags,
        )
        if "av1_nvenc" not in result.stdout:
            log.debug("av1_nvenc: FFmpeg encoder not found in this build")
            return False
    except Exception as e:
        log.debug(f"av1_nvenc: FFmpeg encoder check failed: {e}")
        return False

    log.info(
        "av1_nvenc: GPU and FFmpeg both support AV1 NVENC — enabling ffmpeg_nvenc tool"
    )
    return True


def get_device(use_gpu: bool) -> str:
    """
    Returns "cuda" if use_gpu=True and PyTorch can actually use CUDA, else "cpu".
    Always safe to call — never raises.
    """
    if not use_gpu:
        return "cpu"
    try:
        import torch

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"
