"""Task 009 - unify the two heuristic generators.

Originally this pinned identical bucketing/casting between the standalone CLI
generator and the canonical one. Subsequent tasks converged on a SINGLE
generator: task_019 made the CLI delegate to the canonical function, and task_016
replaced the bucket model with a fitted curve, retiring the legacy
HeuristicGenerator class entirely. What remains worth pinning is that the
canonical generator writes to the exact path the interpolator loads (defect 3
below), so a regenerated table is actually consumed.

Original defects (kept for context):
  1. Resolution-bucket boundary at exactly 8.0 MP diverged between generators.
  2. Quality casting diverged (int-truncation vs rounding).
  3. The generator wrote its table to a path the engine never read.
"""

from pathlib import Path


def test_canonical_table_path_is_interpolator_path():
    from app.core import heuristic as heuristic_mod
    from app.core.config import HEURISTIC_TABLE_PATH

    assert Path(heuristic_mod.OUTPUT_TABLE_PATH) == Path(HEURISTIC_TABLE_PATH)


def test_single_generator_entrypoint():
    # The legacy bucket-shaped HeuristicGenerator was retired; the CLI now exposes
    # only generate_cli, which delegates to the canonical generate_heuristic_table.
    import tools.generate_heuristic_data as cli

    assert hasattr(cli, "generate_cli")
    assert not hasattr(cli, "HeuristicGenerator")
