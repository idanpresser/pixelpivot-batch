# tests/test_trace_propagation.py
from app.core import tracing


def test_hotfolder_trigger_stamps_hotfolder_prefix(monkeypatch):
    tracing.reset_trace_id()
    captured = {}

    # _trigger_batch must stamp a hotfolder- trace before doing work
    from app.batch_api import hot_folder
    handler = hot_folder.HotFolderHandler.__new__(hot_folder.HotFolderHandler)
    monkeypatch.setattr(handler, "_run", lambda: captured.__setitem__("tid", tracing.get_trace_id()), raising=False)
    handler._stamp_trace()  # helper added in impl
    assert tracing.get_trace_id().startswith("hotfolder-")


def test_copy_context_carries_trace_id_into_worker_thread():
    tracing.new_trace_id("req-")
    parent = tracing.get_trace_id()
    seen = []

    def work(_):
        seen.append(tracing.get_trace_id())

    with ThreadPoolExecutor(max_workers=2) as ex:
        list(ex.map(tracing.bind_context(work), [1, 2]))

    assert seen == [parent, parent]


def test_convert_batch_worker_inherits_trace_id(monkeypatch):
    from app.core.converters import base as basemod
    tracing.new_trace_id("req-")
    expected = tracing.get_trace_id()
    seen = []

    class FakeConverter(basemod.BaseConverter):
        def get_name(self): return "fake"
        def supported_formats(self): return ["webp"]
        def convert(self, in_path, out_path, fmt, q, run_id=None):
            seen.append(tracing.get_trace_id())
            return basemod.ConvertResult(success=True, bytes_written=1)

    conv = FakeConverter()
    conv.convert_batch(["a.png", "b.png"], "dummy_out", "webp", [80, 80])
    assert seen and all(t == expected for t in seen)


def test_sharp_request_includes_current_trace_id():
    import json
    from app.core.converters.sharp_converter import build_sharp_request
    tracing.new_trace_id("req-")
    req = build_sharp_request(in_path="a.png", out_path="a.webp", fmt="webp", quality=80)
    assert req["trace_id"] == tracing.get_trace_id()
    assert "\n" not in json.dumps(req)
from concurrent.futures import ThreadPoolExecutor
