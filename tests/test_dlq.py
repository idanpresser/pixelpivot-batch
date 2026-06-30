from pathlib import Path
from app.batch_api.orchestrator import quarantine_to_dlq


def test_quarantine_moves_file_and_returns_record(tmp_path):
    target = tmp_path / "out"
    target.mkdir()
    bad = tmp_path / "broken.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n garbage")  # ASCII only, per project rule

    rec = quarantine_to_dlq(str(bad), str(target), reason="Corrupt PNG chunk")

    dlq_path = target / "corrupt_or_failed" / "broken.png"
    assert dlq_path.exists()           # moved, not copied
    assert not bad.exists()
    assert rec["path"].endswith("broken.png")
    assert rec["reason"] == "Corrupt PNG chunk"
    assert rec["dlq"] is True
