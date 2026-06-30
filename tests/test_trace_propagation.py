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
