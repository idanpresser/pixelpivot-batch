import pytest
from app.core.converters.base import BaseConverter, register_converter, get_converter_registry
from app.batch_api.orchestrator import BatchOrchestrator

def test_converter_registry_discovery():
    registry = get_converter_registry()
    assert "magick" in registry
    assert "ffmpeg" in registry
    assert "vips" in registry
    assert "sharp" in registry
    assert "cavif" in registry

def test_add_new_converter_without_modifying_orchestrator():
    # 1. Define and register a new dummy converter
    @register_converter("dummy_test")
    class DummyTestConverter(BaseConverter):
        def __init__(self, some_custom_path=None):
            super().__init__()
            self.some_custom_path = some_custom_path

        def get_name(self) -> str:
            return "dummy_test"

        def supported_formats(self) -> list[str]:
            return ["png"]

        def convert(self, *args, **kwargs):
            pass

    try:
        # 2. Verify it is registered
        registry = get_converter_registry()
        assert "dummy_test" in registry

        # 3. Instantiate orchestrator
        orchestrator = BatchOrchestrator()
        assert "dummy_test" in orchestrator.converters
        assert isinstance(orchestrator.converters["dummy_test"], DummyTestConverter)

    finally:
        # 4. Clean up registration to avoid polluting other tests
        from app.core.converters.base import _converter_registry
        _converter_registry.pop("dummy_test", None)
