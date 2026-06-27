# tests/core/test_config_direction.py
from app.core import config

def test_direction_default_is_ascending():
    assert config.quality_direction_for("vips", "webp") == "ascending"
    assert config.quality_direction_for("sharp", "jxl") == "ascending"
    assert config.quality_direction_for("magick", "avif") == "ascending"

def test_direction_ffmpeg_avif_is_descending():
    assert config.quality_direction_for("ffmpeg", "avif") == "descending"

def test_direction_is_case_insensitive_on_format():
    assert config.quality_direction_for("ffmpeg", "AVIF") == "descending"
