# tests/core/test_otel.py
import sys
import importlib
import builtins


def test_span_is_noop_and_does_not_import_otel_when_disabled(monkeypatch):
    monkeypatch.setenv("PIXELPIVOT_OTEL_ENABLED", "0")
    # Fresh import so the flag is read now.
    for m in [m for m in list(sys.modules) if m.startswith("app.core.otel")]:
        del sys.modules[m]

    original_import = builtins.__import__
    imported_modules = []

    def mocked_import(name, *args, **kwargs):
        if name == "opentelemetry" or name.startswith("opentelemetry."):
            imported_modules.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", mocked_import)

    import app.core.otel as otel
    importlib.reload(otel)

    with otel.span("quality_curve"):
        pass

    assert not imported_modules


def test_span_yields_when_enabled_but_sdk_absent(monkeypatch):
    # Even if enabled, a missing SDK must degrade to a no-op (air-gapped host).
    monkeypatch.setenv("PIXELPIVOT_OTEL_ENABLED", "1")
    for m in [m for m in list(sys.modules) if m.startswith("app.core.otel")]:
        del sys.modules[m]
    import app.core.otel as otel
    importlib.reload(otel)
    with otel.span("staging"):
        pass  # must not raise regardless of SDK availability
