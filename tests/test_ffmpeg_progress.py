"""Unit tests for the FFmpeg -progress stream parser."""

from __future__ import annotations

from app.core.ffmpeg.progress import ProgressParser


def feed_lines(parser: ProgressParser, lines: list[str]) -> list:
    out = []
    for line in lines:
        sample = parser.feed_line(line)
        if sample is not None:
            out.append(sample)
    return out


def test_continue_block_yields_one_sample_not_done():
    parser = ProgressParser()
    samples = feed_lines(parser, [
        "frame=12",
        "fps=8.42",
        "out_time_us=400000",
        "total_size=51200",
        "bitrate=20.5kbits/s",
        "speed=0.34x",
        "progress=continue",
    ])
    assert len(samples) == 1
    s = samples[0]
    assert s.frame == 12
    assert s.out_time_us == 400000
    assert s.total_size == 51200
    assert s.speed == 0.34
    assert s.bitrate_kbps == 20.5
    assert s.done is False


def test_end_block_marks_done():
    parser = ProgressParser()
    samples = feed_lines(parser, [
        "frame=1",
        "fps=0.5",
        "progress=end",
    ])
    assert len(samples) == 1
    assert samples[0].done is True


def test_two_blocks_in_one_stream():
    parser = ProgressParser()
    samples = feed_lines(parser, [
        "frame=1", "progress=continue",
        "frame=2", "progress=end",
    ])
    assert len(samples) == 2
    assert samples[0].frame == 1 and samples[0].done is False
    assert samples[1].frame == 2 and samples[1].done is True


def test_blank_and_garbage_lines_are_ignored():
    parser = ProgressParser()
    samples = feed_lines(parser, [
        "",
        "not a key value pair",
        "frame=7",
        "progress=continue",
    ])
    assert len(samples) == 1
    assert samples[0].frame == 7


def test_na_values_become_zero():
    parser = ProgressParser()
    samples = feed_lines(parser, [
        "frame=N/A",
        "fps=N/A",
        "out_time_us=N/A",
        "total_size=N/A",
        "bitrate=N/A",
        "speed=N/A",
        "progress=continue",
    ])
    assert len(samples) == 1
    s = samples[0]
    assert s.frame == 0
    assert s.fps == 0.0
    assert s.total_size == 0
    assert s.speed == 0.0


def test_missing_optional_fields_default_to_zero():
    parser = ProgressParser()
    samples = feed_lines(parser, ["frame=3", "progress=end"])
    assert len(samples) == 1
    s = samples[0]
    assert s.frame == 3
    assert s.fps == 0.0
    assert s.total_size == 0


def test_buffer_resets_between_blocks():
    parser = ProgressParser()
    feed_lines(parser, ["frame=99", "progress=continue"])
    samples = feed_lines(parser, ["fps=1.0", "progress=continue"])
    assert len(samples) == 1
    assert samples[0].frame == 0
    assert samples[0].fps == 1.0


def test_lines_with_trailing_whitespace():
    parser = ProgressParser()
    samples = feed_lines(parser, [
        "frame=5  \n",
        "  progress=end\n",
    ])
    assert len(samples) == 1
    assert samples[0].frame == 5
    assert samples[0].done is True
