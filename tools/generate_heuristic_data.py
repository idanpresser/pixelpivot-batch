"""Generate Heuristic Data — CLI wrapper for on-demand heuristic table regeneration.

Thin entrypoint that delegates to app.core.heuristic.generate_heuristic_table
so the emitted table is produced by a single source of truth (schema, version,
curve fit, gate).
"""

import sqlite3
from typing import Optional


def generate_cli(db_path: str, output_json: str, weights_path: Optional[str] = None) -> dict:
    """CLI entrypoint: regenerate heuristic table from a database.

    Delegates to the canonical generator so the emitted table is produced by a
    single source of truth (schema, version, curve fit, gate). The weights file
    defaults to a sibling of the output table so a CLI run is self-contained.

    Args:
        db_path: Path to SQLite database with conversion history.
        output_json: Path to write heuristic_table.json.
        weights_path: Path to write heuristic_weights.json (default: sibling of output).

    Returns:
        Dict with "heuristic_table" and "heuristic_weights" keys.
    """
    from pathlib import Path
    from app.core.heuristic import generate_heuristic_table

    out_path = Path(output_json)
    if weights_path is None:
        weights_path = out_path.parent / "heuristic_weights.json"

    conn = sqlite3.connect(db_path)
    try:
        return generate_heuristic_table(
            conn=conn, table_path=out_path, weights_path=weights_path
        )
    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("Usage: python generate_heuristic_data.py <db_path> <output_json>")
        sys.exit(1)

    generate_cli(sys.argv[1], sys.argv[2])
    print(f"Generated heuristic table saved to {sys.argv[2]}")
