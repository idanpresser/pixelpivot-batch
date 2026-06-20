"""Lock-in tests for bd-bq7 acceptance: OS-core reservation + env-configurable
worker ceiling in the adaptive thread pool (app/core/converters/base.py).

These assert the two acceptance behaviors required by the bead:
  1. Cores are reserved for the OS/API process (workers < cpu_count * factor).
  2. An env-var hard ceiling (PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS) caps
     worker count regardless of CPU count.

The memory-aware cap and RAM guard are neutralized here so the assertions
isolate the CPU-reservation / ceiling logic specifically.
"""
from unittest.mock import MagicMock, patch

from app.core.converters.base import BaseConverter


class DummyConverter(BaseConverter):
    def get_name(self) -> str:
        return "dummy"

    def supported_formats(self) -> list:
        return ["jpg"]

    def convert(self, *args, **kwargs):
        return {"success": True}


def _run_and_capture_workers(cpu_count, num_files, ram_gb=64.0):
    """Run a batch convert with RAM/memory-cap neutralized; return max_workers."""
    conv = DummyConverter()
    mock_vm = MagicMock()
    mock_vm.available = int(ram_gb * 1024 * 1024 * 1024)
    with patch("os.cpu_count", return_value=cpu_count), \
         patch("psutil.virtual_memory", return_value=mock_vm), \
         patch("app.core.converters.base.memory_aware_worker_cap",
               side_effect=lambda base, *a, **k: base), \
         patch("app.core.converters.base.ThreadPoolExecutor") as mock_exec:
        conv._default_batch_convert(["a.jpg"] * num_files, "out", "jpg", [80] * num_files)
        return mock_exec.call_args[1]["max_workers"]


def test_workers_reserve_cores_for_os_when_many_cpus():
    """On a 16-core host, 2 cores are reserved: (16-2)*2.0 = 28, not 32."""
    workers = _run_and_capture_workers(cpu_count=16, num_files=100)
    assert workers == 28


def test_workers_reserve_one_core_on_small_host():
    """On a 4-core host, 1 core is reserved: (4-1)*2.0 = 6, not 8."""
    workers = _run_and_capture_workers(cpu_count=4, num_files=100)
    assert workers == 6


def test_env_max_workers_ceiling_caps_scaling():
    """Env ceiling overrides CPU-derived scaling regardless of core count."""
    with patch("app.core.converters.base.CONCURRENT_ENCODES_MAX_WORKERS", 5):
        workers = _run_and_capture_workers(cpu_count=32, num_files=100)
    assert workers == 5
