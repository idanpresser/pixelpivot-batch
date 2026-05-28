import pytest
import os
import time
from typing import List, Dict, Any, Union
from app.core.converters.base import BaseConverter

class DummyConverter(BaseConverter):
    def get_name(self) -> str:
        return "dummy"
    
    def supported_formats(self) -> List[str]:
        return ["webp"]
    
    def convert(
        self,
        input_path: str,
        output_path: str,
        target_format: str,
        quality: Union[int, float],
        **kwargs
    ) -> Dict[str, Any]:
        # Simulate work
        time.sleep(0.01)
        # Create a dummy output file if requested
        with open(output_path, "w") as f:
            f.write("dummy")
        return {
            "success": True,
            "duration_ms": 10.0,
            "telemetry": {"cpu_avg": 1.0},
            "parameters_used": {"quality": quality},
            "error": None
        }
    
    # We will let it use the default convert_batch implementation once added

def test_default_batch_convert(tmp_path):
    conv = DummyConverter()
    input_paths = [str(tmp_path / f"in_{i}.txt") for i in range(3)]
    for p in input_paths:
        with open(p, "w") as f:
            f.write("test")
    
    output_dir = str(tmp_path / "out")
    os.makedirs(output_dir, exist_ok=True)
    
    qualities = [80.0, 70.0, 60.0]
    
    # This should work once we implement the default in BaseConverter
    result = conv.convert_batch(input_paths, output_dir, "webp", qualities)
    
    assert result["success_count"] == 3
    assert result["failure_count"] == 0
    assert result["duration_ms"] > 0
    assert len(result["errors"]) == 0
    
    # Check if files were created
    for i in range(3):
        # The filename in output_dir should be in_i.webp
        expected_out = os.path.join(output_dir, f"in_{i}.webp")
        assert os.path.exists(expected_out)

def test_batch_convert_with_failures(tmp_path):
    class FailingDummy(DummyConverter):
        def convert(self, input_path, output_path, *args, **kwargs):
            if "fail_1.txt" in os.path.basename(input_path):
                return {"success": False, "error": "forced failure", "duration_ms": 0}
            return super().convert(input_path, output_path, *args, **kwargs)

    conv = FailingDummy()
    input_paths = [
        str(tmp_path / "pass_1.txt"),
        str(tmp_path / "fail_1.txt"),
        str(tmp_path / "pass_2.txt")
    ]
    for p in input_paths:
        with open(p, "w") as f:
            f.write("test")
    
    output_dir = str(tmp_path / "out_fail")
    os.makedirs(output_dir, exist_ok=True)
    
    result = conv.convert_batch(input_paths, output_dir, "webp", [80, 80, 80])
    
    print(f"DEBUG Result: {result}")
    
    assert result["success_count"] == 2
    assert result["failure_count"] == 1
    assert len(result["errors"]) == 1
    assert "forced failure" in result["errors"][0]["error"]
