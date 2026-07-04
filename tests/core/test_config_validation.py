"""Tests for startup config validation + fail-fast (E8 8.2).

~10 env tunables are consumed across app.core.config and the converter base
with no boot-time validation, so a bad value fails deep inside a batch. These
tests pin the behavior of a single validate-at-boot gate that fails fast with a
clear, var-named error and reports the resolved effective config.
"""
import logging

import pytest

from app.core.config_validation import (
    ConfigValidationError,
    validate_startup_config,
    validate_and_log_startup_config,
)


def test_validate_raises_clear_error_when_ram_headroom_not_a_float():
    with pytest.raises(ConfigValidationError) as exc:
        validate_startup_config({"PIXELPIVOT_WORKER_RAM_HEADROOM": "abc"})
    msg = str(exc.value)
    assert "PIXELPIVOT_WORKER_RAM_HEADROOM" in msg
    assert "abc" in msg


def test_validate_raises_when_ram_headroom_out_of_range():
    with pytest.raises(ConfigValidationError) as exc:
        validate_startup_config({"PIXELPIVOT_WORKER_RAM_HEADROOM": "2.0"})
    assert "PIXELPIVOT_WORKER_RAM_HEADROOM" in str(exc.value)


def test_validate_raises_when_abort_threshold_below_one():
    with pytest.raises(ConfigValidationError) as exc:
        validate_startup_config({"PIXELPIVOT_BATCH_FATAL_ABORT_THRESHOLD": "0"})
    assert "PIXELPIVOT_BATCH_FATAL_ABORT_THRESHOLD" in str(exc.value)


def test_validate_raises_when_int_tunable_not_an_int():
    with pytest.raises(ConfigValidationError) as exc:
        validate_startup_config({"PIXELPIVOT_WORKER_BYTES_PER_PX": "1.5"})
    assert "PIXELPIVOT_WORKER_BYTES_PER_PX" in str(exc.value)


def test_validate_returns_resolved_defaults_when_env_empty():
    resolved = validate_startup_config({})
    assert resolved["PIXELPIVOT_WORKER_RAM_HEADROOM"] == 0.7
    assert resolved["PIXELPIVOT_BATCH_FATAL_ABORT_THRESHOLD"] == 3
    assert resolved["PIXELPIVOT_SHUTDOWN_GRACE_S"] == 30.0


def test_validate_uses_env_override_when_valid():
    resolved = validate_startup_config({"PIXELPIVOT_SHUTDOWN_GRACE_S": "10"})
    assert resolved["PIXELPIVOT_SHUTDOWN_GRACE_S"] == 10.0


def test_optional_max_workers_defaults_to_none_but_validates_when_present():
    assert validate_startup_config({})["PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS"] is None
    assert (
        validate_startup_config(
            {"PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS": "4"}
        )["PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS"]
        == 4
    )
    with pytest.raises(ConfigValidationError):
        validate_startup_config({"PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS": "0"})


def test_app_import_fails_fast_on_bad_env(monkeypatch):
    # Service-level: importing the FastAPI app with a malformed tunable must
    # raise the clear ConfigValidationError at import — before the converter
    # base parses the same var into a cryptic bare ValueError.
    import importlib
    import app.batch_api.main as main_mod

    monkeypatch.setenv("PIXELPIVOT_WORKER_RAM_HEADROOM", "abc")
    with pytest.raises(ConfigValidationError):
        importlib.reload(main_mod)
    # Restore a clean module for the rest of the session.
    monkeypatch.delenv("PIXELPIVOT_WORKER_RAM_HEADROOM", raising=False)
    importlib.reload(main_mod)


def test_validate_and_log_emits_resolved_config_once(caplog):
    with caplog.at_level(logging.INFO):
        resolved = validate_and_log_startup_config({})
    lines = [r for r in caplog.records if "effective config" in r.getMessage().lower()]
    assert len(lines) == 1
    assert resolved["PIXELPIVOT_WORKER_RAM_HEADROOM"] == 0.7
