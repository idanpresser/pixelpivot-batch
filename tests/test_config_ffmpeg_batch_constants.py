from app.core import config


def test_image2_threshold_is_positive_int():
    assert isinstance(config.IMAGE2_THRESHOLD, int)
    assert config.IMAGE2_THRESHOLD >= 2


def test_ffmpeg_batch_max_files_is_reasonable():
    assert isinstance(config.FFMPEG_BATCH_MAX_FILES, int)
    assert 10 <= config.FFMPEG_BATCH_MAX_FILES <= 1000


def test_ffmpeg_batch_max_cmdline_bytes_safe_for_windows():
    # Windows CreateProcess has an 8191-char limit; keep margin for env + ffmpeg path.
    assert isinstance(config.FFMPEG_BATCH_MAX_CMDLINE_BYTES, int)
    assert 4000 <= config.FFMPEG_BATCH_MAX_CMDLINE_BYTES <= 8000


def test_magick_mogrify_max_cmdline_bytes_safe_for_windows():
    assert isinstance(config.MAGICK_MOGRIFY_MAX_CMDLINE_BYTES, int)
    assert 4000 <= config.MAGICK_MOGRIFY_MAX_CMDLINE_BYTES <= 8000
