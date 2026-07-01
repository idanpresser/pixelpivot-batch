# tests/db/test_priority_claim.py
from app.core.db.connection import get_connection
from app.core.db.schema import init_db
from app.core.db.repositories.batch import BatchRepository


def _mk(conn, repo, priority):
    return repo.create_run(conn, source_dir="s", target_dir="t", target_format="webp",
                           tool="ffmpeg", trigger_type="api", status="queued", priority=priority)


def test_claim_returns_high_priority_first():
    init_db()
    repo = BatchRepository()
    with get_connection() as conn:
        low = _mk(conn, repo, 0)
        high = _mk(conn, repo, 100)
    claimed = repo.claim_next_queued(get_connection)
    assert claimed is not None
    assert claimed["id"] == high  # priority DESC beats insertion order


def test_claim_is_atomic_single_winner():
    init_db()
    repo = BatchRepository()
    with get_connection() as conn:
        rid = _mk(conn, repo, 50)
    first = repo.claim_next_queued(get_connection)
    second = repo.claim_next_queued(get_connection)
    assert first is not None and first["id"] == rid
    # Row already claimed (status flipped to 'running'); second sees nothing.
    assert second is None or second["id"] != rid


def test_claim_none_when_empty():
    init_db()
    repo = BatchRepository()
    # Drain anything left queued from prior tests.
    while repo.claim_next_queued(get_connection):
        pass
    assert repo.claim_next_queued(get_connection) is None
