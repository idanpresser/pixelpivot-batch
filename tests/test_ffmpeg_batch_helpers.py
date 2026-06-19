import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from app.core.converters.ffmpeg_batch_helpers import (
    all_same_resolution,
    build_image2_args,
    build_multimap_args,
    encoder_params_for,
    group_by_dimensions,
    pack_chunks,
    stage_inputs_for_image2,
    staging_dir,
)


def test_group_by_dimensions_buckets_identical_sizes():
    paths = ["a.png", "b.png", "c.png"]
    sizes = {"a.png": (1920, 1080), "b.png": (1920, 1080), "c.png": (800, 600)}

    with patch(
        "app.core.converters.ffmpeg_batch_helpers.probe_image_dimensions",
        side_effect=lambda p: sizes[p],
    ):
        groups = group_by_dimensions(paths)

    assert groups == {
        (1920, 1080): ["a.png", "b.png"],
        (800, 600): ["c.png"],
    }


def test_group_by_dimensions_skips_unprobeable_files():
    paths = ["good.png", "broken.png"]

    def fake_probe(p):
        if p == "broken.png":
            raise OSError("unreadable")
        return (640, 480)

    with patch(
        "app.core.converters.ffmpeg_batch_helpers.probe_image_dimensions",
        side_effect=fake_probe,
    ):
        groups = group_by_dimensions(paths)

    assert groups[(640, 480)] == ["good.png"]
    assert groups[None] == ["broken.png"]


def test_group_by_dimensions_empty_input():
    assert group_by_dimensions([]) == {}


def test_group_by_dimensions_orders_subgroups_by_pixel_count_desc():
    paths = ["tiny.png", "huge.png", "medium.png"]
    sizes = {
        "tiny.png":   (100, 100),
        "huge.png":   (4000, 3000),
        "medium.png": (800, 600),
    }
    with patch(
        "app.core.converters.ffmpeg_batch_helpers.probe_image_dimensions",
        side_effect=lambda p: sizes[p],
    ):
        groups = group_by_dimensions(paths)

    keys = list(groups.keys())
    assert keys == [(4000, 3000), (800, 600), (100, 100)]


def test_group_by_dimensions_paths_sorted_alphabetically_within_bucket():
    paths = ["c.png", "a.png", "b.png"]
    with patch(
        "app.core.converters.ffmpeg_batch_helpers.probe_image_dimensions",
        return_value=(500, 500),
    ):
        groups = group_by_dimensions(paths)
    assert groups[(500, 500)] == ["a.png", "b.png", "c.png"]


def test_group_by_dimensions_unprobeable_bucket_comes_last():
    paths = ["good_big.png", "broken.png", "good_small.png"]

    def fake_probe(p):
        if p == "broken.png":
            raise OSError("nope")
        return (2000, 2000) if "big" in p else (200, 200)

    with patch(
        "app.core.converters.ffmpeg_batch_helpers.probe_image_dimensions",
        side_effect=fake_probe,
    ):
        groups = group_by_dimensions(paths)

    keys = list(groups.keys())
    assert keys[-1] is None
    assert keys[0] == (2000, 2000)


def _make_temp_file(dir_: str, name: str, content: bytes = b"x") -> str:
    p = Path(dir_) / name
    p.write_bytes(content)
    return str(p)


def test_stage_inputs_creates_sequential_names_and_rename_map(tmp_path):
    src_a = _make_temp_file(str(tmp_path), "alpha.png", b"AAA")
    src_b = _make_temp_file(str(tmp_path), "beta.png",  b"BBB")
    src_c = _make_temp_file(str(tmp_path), "gamma.png", b"CCC")

    stage = tmp_path / "stage"
    stage.mkdir()

    rename_map = stage_inputs_for_image2([src_a, src_b, src_c], str(stage), ext="png")

    assert (stage / "frame00001.png").exists()
    assert (stage / "frame00002.png").exists()
    assert (stage / "frame00003.png").exists()

    assert (stage / "frame00001.png").read_bytes() == b"AAA"
    assert (stage / "frame00002.png").read_bytes() == b"BBB"
    assert (stage / "frame00003.png").read_bytes() == b"CCC"

    assert rename_map == {1: "alpha", 2: "beta", 3: "gamma"}


def test_staging_dir_context_manager_cleans_up_even_on_exception():
    captured_path = {}

    try:
        with staging_dir(prefix="ffbatch_") as d:
            captured_path["path"] = d
            assert os.path.isdir(d)
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert not os.path.exists(captured_path["path"])


def test_stage_inputs_copy_fallback_when_hardlink_fails(tmp_path, monkeypatch):
    src = _make_temp_file(str(tmp_path), "only.png", b"ZZZ")
    stage = tmp_path / "stage"
    stage.mkdir()

    def fail_link(src_, dst_):
        raise OSError("simulated cross-device link")

    monkeypatch.setattr("app.core.converters.ffmpeg_batch_helpers.os.link", fail_link)

    rename_map = stage_inputs_for_image2([src], str(stage), ext="png")

    assert (stage / "frame00001.png").read_bytes() == b"ZZZ"
    assert rename_map == {1: "only"}


def test_build_image2_args_webp():
    args = build_image2_args(
        staging_dir_path="/tmp/stage",
        input_ext="png",
        output_ext="webp",
        count=5,
        encoder_params=["-c:v", "libwebp", "-quality", "80"],
    )

    assert args[0:2] == ["-y", "-hide_banner"]
    assert "-f" in args and args[args.index("-f") + 1] == "image2"
    assert "-start_number" in args and args[args.index("-start_number") + 1] == "1"
    # Path normalization: forward slashes always.
    assert "/tmp/stage/frame%05d.png" in args
    for tok in ["-c:v", "libwebp", "-quality", "80"]:
        assert tok in args
    assert args[-1] == "/tmp/stage/out%05d.webp"


def test_build_image2_args_truncates_with_vframes_for_safety():
    args = build_image2_args(
        staging_dir_path="/tmp/stage",
        input_ext="png",
        output_ext="avif",
        count=10,
        encoder_params=["-c:v", "libaom-av1", "-crf", "30"],
    )
    assert "-vframes" in args
    assert args[args.index("-vframes") + 1] == "10"


def test_build_image2_args_vframes_wins_over_encoder_params():
    """If encoder_params contains its own -vframes, our truncation guard must
    still apply -- ffmpeg honors the LAST occurrence, so our -vframes must
    come AFTER the spread of encoder_params."""
    args = build_image2_args(
        staging_dir_path="/tmp/stage",
        input_ext="png",
        output_ext="webp",
        count=7,
        encoder_params=["-c:v", "libwebp", "-vframes", "9999"],
    )
    # Last -vframes wins -- find the last index and confirm its value is 7.
    last_vframes = max(i for i, tok in enumerate(args) if tok == "-vframes")
    assert args[last_vframes + 1] == "7"


def test_pack_chunks_respects_max_files():
    pairs = [(f"in{i}.png", f"out{i}.webp") for i in range(250)]
    chunks = pack_chunks(pairs, max_files=100, max_cmdline_bytes=10_000_000, fixed_overhead=200)
    assert len(chunks) == 3
    assert len(chunks[0]) == 100
    assert len(chunks[1]) == 100
    assert len(chunks[2]) == 50


def test_pack_chunks_respects_max_cmdline_bytes():
    long_name = "x" * 200
    pairs = [(f"{long_name}{i}.png", f"{long_name}{i}.webp") for i in range(20)]
    chunks = pack_chunks(pairs, max_files=100, max_cmdline_bytes=2000, fixed_overhead=200)
    assert all(len(c) >= 1 for c in chunks)
    assert sum(len(c) for c in chunks) == 20
    for chunk in chunks:
        approx = 200 + sum(len(i) + len(o) + 20 for i, o in chunk)
        assert approx <= 2000 or len(chunk) == 1


def test_pack_chunks_never_loses_input():
    pairs = [(f"a{i}.png", f"b{i}.webp") for i in range(17)]
    chunks = pack_chunks(pairs, max_files=5, max_cmdline_bytes=999_999, fixed_overhead=0)
    flat = [p for c in chunks for p in c]
    assert flat == pairs


def test_build_multimap_args_pairs_inputs_and_maps_correctly():
    chunk = [("a.png", "a.webp"), ("b.png", "b.webp"), ("c.png", "c.webp")]
    encoder_params = ["-c:v", "libwebp", "-quality", "75"]

    args = build_multimap_args(chunk, encoder_params)

    assert args.count("-i") == 3
    assert args.count("-map") == 3
    assert args.count("-map_metadata") == 3
    assert args.count("-c:v") == 3
    assert args.count("libwebp") == 3
    assert "a.webp" in args
    assert "b.webp" in args
    assert "c.webp" in args
    # Verify metadata is mapped per index
    assert args[args.index("a.webp") - 6] == "-map_metadata"
    assert args[args.index("a.webp") - 5] == "0"


def test_encoder_params_webp():
    assert encoder_params_for("webp", 80) == ["-c:v", "libwebp", "-quality", "80"]


def test_encoder_params_avif_default_cpu_used_is_4():
    assert encoder_params_for("avif", 30) == ["-c:v", "libaom-av1", "-crf", "30", "-cpu-used", "4"]


def test_encoder_params_jxl_uses_distance():
    params = encoder_params_for("jxl", 80)
    assert params[0:2] == ["-c:v", "libjxl"]
    assert "-distance" in params
    assert "-pix_fmt" in params


def test_encoder_params_unsupported_format_returns_none():
    assert encoder_params_for("xyz", 50) is None


def test_all_same_resolution_true_for_uniform_group(monkeypatch):
    monkeypatch.setattr(
        "app.core.converters.ffmpeg_batch_helpers.probe_image_dimensions",
        lambda p: (1920, 1080),
    )
    assert all_same_resolution(["a.png", "b.png", "c.png"]) is True


def test_all_same_resolution_false_when_one_differs(monkeypatch):
    sizes = {"a.png": (1920, 1080), "b.png": (1920, 1080), "c.png": (1920, 1081)}
    monkeypatch.setattr(
        "app.core.converters.ffmpeg_batch_helpers.probe_image_dimensions",
        lambda p: sizes[p],
    )
    assert all_same_resolution(list(sizes.keys())) is False


def test_all_same_resolution_false_when_probe_fails(monkeypatch):
    def fake(p):
        if p == "broken.png":
            raise OSError("nope")
        return (500, 500)
    monkeypatch.setattr(
        "app.core.converters.ffmpeg_batch_helpers.probe_image_dimensions",
        fake,
    )
    assert all_same_resolution(["good.png", "broken.png"]) is False


def test_all_same_resolution_true_for_singleton():
    assert all_same_resolution(["only.png"]) is True


def test_all_same_resolution_true_for_empty():
    assert all_same_resolution([]) is True
