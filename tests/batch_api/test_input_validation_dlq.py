"""E9 9.1 — unified input validation + quarantine to DLQ.

Malformed inputs must be rejected consistently across all tools (via the shared
partition gate, which runs once before any converter) and routed to the DLQ
with a reason, rather than only logged. The edge-case set (bad_header, empty,
huge) exercises both rejection reasons.
"""
from pathlib import Path

from app.batch_api.image_guards import partition_images
from app.batch_api.orchestrator import quarantine_rejected


def test_edge_case_set_rejected_uniformly_by_shared_gate():
    # bad_header + empty probe to (0,0) -> unreadable; huge is 56 MP -> exceeds
    # MASSIVE_IMAGE_THRESHOLD. The gate takes no tool argument, so every tool
    # sees the same usable set and identical reject counts by construction.
    dim_cache = {
        "bad_header": (0, 0),
        "empty": (0, 0),
        "huge": (8000, 7000),  # 56 MP > 50 MP hard stop
        "good": (100, 100),
    }
    usable, rejected = partition_images(list(dim_cache), dim_cache)
    assert usable == ["good"]
    assert {r["path"] for r in rejected} == {"bad_header", "empty", "huge"}


def test_quarantine_rejected_moves_files_to_dlq_with_reason(tmp_path):
    target = tmp_path / "out"
    target.mkdir()
    bad = tmp_path / "bad_header.png"
    bad.write_bytes(b"not really a png")  # ASCII only, per project rule
    empty = tmp_path / "empty.png"
    empty.write_bytes(b"")
    rejected = [
        {"path": str(bad), "error": "unreadable or corrupt"},
        {"path": str(empty), "error": "unreadable or corrupt"},
    ]

    recs = quarantine_rejected(rejected, str(target))

    assert len(recs) == 2
    for rec in recs:
        assert rec["dlq"] is True
        assert rec["error"] == "unreadable or corrupt"
        assert (target / "corrupt_or_failed" / Path(rec["path"]).name).exists()
    assert not bad.exists()
    assert not empty.exists()


def test_quarantine_rejected_passes_through_pathless_records(tmp_path):
    target = tmp_path / "out"
    target.mkdir()
    rejected = [{"path": "N/A", "error": "Unsupported tool: bogus"}]
    recs = quarantine_rejected(rejected, str(target))
    assert recs == rejected  # nothing to move; recorded as-is
