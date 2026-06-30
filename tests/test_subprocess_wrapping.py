# tests/test_subprocess_wrapping.py
import logging
from app.core.converters.base import build_subprocess_log_payload


def test_payload_nests_raw_output_and_parsed_error():
    payload = build_subprocess_log_payload(
        tool_name="ffmpeg",
        returncode=1,
        stderr="frame=  1\n[error] Invalid data found when processing input\n",
    )
    assert payload["tool"] == "ffmpeg"
    assert payload["returncode"] == 1
    assert "Invalid data found" in payload["error"]
    assert "frame=" in payload["raw_output"]
    assert "\n" not in payload["error"]  # single concise error line


def test_payload_no_error_on_success():
    payload = build_subprocess_log_payload("ffmpeg", 0, "frame=  1\n")
    assert payload["returncode"] == 0
    assert payload.get("error") is None
