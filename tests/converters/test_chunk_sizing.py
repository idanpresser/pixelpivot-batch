# tests/converters/test_chunk_sizing.py
from app.core.converters.chunk_sizing import dynamic_max_files


def test_higher_mp_gives_smaller_chunk_same_budget():
    budget = 1_000_000_000  # 1 GB
    small = dynamic_max_files(megapixels=1.0, ram_budget_bytes=budget, ceiling=20)
    large = dynamic_max_files(megapixels=25.0, ram_budget_bytes=budget, ceiling=20)
    assert large < small


def test_never_exceeds_ceiling():
    huge_budget = 10**12
    assert dynamic_max_files(megapixels=0.1, ram_budget_bytes=huge_budget, ceiling=20) == 20


def test_never_below_one():
    assert dynamic_max_files(megapixels=500.0, ram_budget_bytes=1, ceiling=20) == 1


def test_formula_matches_4x_rgba():
    # peak RAM ~= 4 * megapixels * 1e6 bytes per in-flight image.
    # budget for exactly 4 images of 10 MP: 4 * (4 * 10 * 1e6) = 160 MB
    budget = 4 * (4 * 10 * 1_000_000)
    assert dynamic_max_files(megapixels=10.0, ram_budget_bytes=budget, ceiling=20) == 4
