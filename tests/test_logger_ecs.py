# tests/test_logger_ecs.py
import json
import logging
from app.core.logger import EcsJsonFormatter
from app.core import tracing


def test_ecs_formatter_emits_single_line_json_with_ecs_keys():
    tracing.new_trace_id("req-")
    fmt = EcsJsonFormatter(service_name="pixelpivot-api")
    rec = logging.LogRecord("core.test", logging.INFO, __file__, 10, "hello", None, None)
    rec.trace_id = tracing.get_trace_id()
    rec.batch = {"run_id": 1042, "tool": "ffmpeg", "format": "avif"}
    line = fmt.format(rec)
    assert "\n" not in line
    obj = json.loads(line)
    assert obj["log.level"] == "INFO"
    assert obj["message"] == "hello"
    assert obj["service.name"] == "pixelpivot-api"
    assert obj["trace.id"].startswith("req-")
    assert obj["batch.run_id"] == 1042
    assert "@timestamp" in obj


def test_text_is_default_format(monkeypatch):
    monkeypatch.delenv("PIXELPIVOT_LOG_FORMAT", raising=False)
    from app.core import logger as logmod
    assert logmod._selected_formatter().__class__.__name__ == "Formatter"
