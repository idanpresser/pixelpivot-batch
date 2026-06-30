# tests/test_tracing.py
import logging
from app.core import tracing


def test_new_trace_id_uses_prefix_and_sets_contextvar():
    tid = tracing.new_trace_id("req-")
    assert tid.startswith("req-")
    assert tracing.get_trace_id() == tid


def test_get_trace_id_is_none_when_unset():
    tracing.reset_trace_id()
    assert tracing.get_trace_id() is None


def test_filter_injects_current_trace_id():
    tid = tracing.new_trace_id("req-")
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    assert tracing.TraceIdFilter().filter(rec) is True
    assert rec.trace_id == tid


def test_filter_fallback_generates_when_unset():
    tracing.reset_trace_id()
    rec = logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None)
    tracing.TraceIdFilter().filter(rec)
    assert rec.trace_id.startswith("system-")
    # fallback also pins it so subsequent lines in the same context match
    assert tracing.get_trace_id() == rec.trace_id
