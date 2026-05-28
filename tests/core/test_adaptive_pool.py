import os
import pytest
from unittest.mock import MagicMock, patch
from app.core.converters.base import BaseConverter

class DummyConverter(BaseConverter):
    def get_name(self) -> str: return "dummy"
    def supported_formats(self) -> list: return ["jpg"]
    def convert(self, *args, **kwargs): return {"success": True}

def test_adaptive_thread_pool_scaling():
    """
    Verify that thread pool scales with CPU count.
    """
    conv = DummyConverter()
    
    # 4 cores -> should have some N workers
    with patch("os.cpu_count", return_value=4), \
         patch("app.core.converters.base.ThreadPoolExecutor") as mock_executor_cls:
        
        conv._default_batch_convert(["a.jpg"]*10, "out", "jpg", [80]*10)
        
        max_workers_4 = mock_executor_cls.call_args[1]["max_workers"]
        
    # 16 cores -> should have more workers
    with patch("os.cpu_count", return_value=16), \
         patch("app.core.converters.base.ThreadPoolExecutor") as mock_executor_cls:
        
        conv._default_batch_convert(["a.jpg"]*100, "out", "jpg", [80]*100)
        
        max_workers_16 = mock_executor_cls.call_args[1]["max_workers"]
        
    assert max_workers_16 > max_workers_4

def test_resource_guard_restricts_growth():
    """
    Verify that low RAM restricts the thread pool size.
    """
    conv = DummyConverter()
    
    # Normal RAM
    mock_vm = MagicMock()
    mock_vm.available = 1024 * 1024 * 4096 # 4 GB
    
    with patch("os.cpu_count", return_value=16), \
         patch("psutil.virtual_memory", return_value=mock_vm), \
         patch("app.core.converters.base.ThreadPoolExecutor") as mock_executor_cls:
        
        conv._default_batch_convert(["a.jpg"]*100, "out", "jpg", [80]*100)
        max_workers_normal = mock_executor_cls.call_args[1]["max_workers"]

    # Low RAM
    mock_vm.available = 1024 * 1024 * 100 # 100 MB
    
    with patch("os.cpu_count", return_value=16), \
         patch("psutil.virtual_memory", return_value=mock_vm), \
         patch("app.core.converters.base.ThreadPoolExecutor") as mock_executor_cls:
        
        conv._default_batch_convert(["a.jpg"]*100, "out", "jpg", [80]*100)
        max_workers_low = mock_executor_cls.call_args[1]["max_workers"]
        
    assert max_workers_low < max_workers_normal
