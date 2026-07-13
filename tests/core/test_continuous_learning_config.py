# tests/core/test_continuous_learning_config.py
"""Tests for continuous learning configuration constants."""

from app.core import config
from pathlib import Path


def test_continuous_learning_constants_loaded():
    """Verify all continuous learning constants are present and have sensible values."""
    assert hasattr(config, "ONLINE_LEARNING_ENABLED")
    assert hasattr(config, "VERIFY_SAMPLE_RATE")
    assert hasattr(config, "VERIFY_MAX_PER_CELL")
    assert hasattr(config, "VERIFY_MIN_FOR_NUDGE")
    assert hasattr(config, "NUDGE_GAIN_K")
    assert hasattr(config, "NUDGE_LEAK_LAMBDA")
    assert hasattr(config, "NUDGE_MAX_OFFSET")
    assert hasattr(config, "BOOTSTRAP_ENABLED")
    assert hasattr(config, "BOOTSTRAP_SAMPLE_N")
    assert hasattr(config, "HEURISTIC_ADJUST_PATH")


def test_online_learning_defaults():
    """Verify online learning is disabled by default."""
    assert config.ONLINE_LEARNING_ENABLED is False


def test_verify_sample_rate_valid_range():
    """Verify sample rate is between 0 and 1."""
    assert 0.0 <= config.VERIFY_SAMPLE_RATE <= 1.0
    assert config.VERIFY_SAMPLE_RATE > 0


def test_verify_max_per_cell_positive():
    """Verify max samples per cell is a positive integer."""
    assert isinstance(config.VERIFY_MAX_PER_CELL, int)
    assert config.VERIFY_MAX_PER_CELL > 0


def test_verify_min_for_nudge_positive():
    """Verify minimum samples for nudge is a positive integer."""
    assert isinstance(config.VERIFY_MIN_FOR_NUDGE, int)
    assert config.VERIFY_MIN_FOR_NUDGE > 0
    assert config.VERIFY_MIN_FOR_NUDGE <= config.VERIFY_MAX_PER_CELL


def test_nudge_gain_positive():
    """Verify nudge gain is positive."""
    assert config.NUDGE_GAIN_K > 0


def test_nudge_leak_valid_range():
    """Verify nudge leak is in a reasonable range [0, 1]."""
    assert 0.0 <= config.NUDGE_LEAK_LAMBDA <= 1.0


def test_nudge_max_offset_positive():
    """Verify nudge max offset is positive."""
    assert config.NUDGE_MAX_OFFSET > 0


def test_bootstrap_enabled_default():
    """Verify bootstrap is enabled by default."""
    assert config.BOOTSTRAP_ENABLED is True


def test_bootstrap_sample_n_positive():
    """Verify bootstrap sample count is a positive integer."""
    assert isinstance(config.BOOTSTRAP_SAMPLE_N, int)
    assert config.BOOTSTRAP_SAMPLE_N > 0


def test_heuristic_adjust_path_is_path():
    """Verify heuristic adjust path is a Path object."""
    assert isinstance(config.HEURISTIC_ADJUST_PATH, Path)


def test_heuristic_adjust_path_parent_is_heuristic_table_parent():
    """Verify heuristic adjust path is in the data directory."""
    from app.core.paths import resolve_data_dir
    assert config.HEURISTIC_ADJUST_PATH.parent == resolve_data_dir()
