import os
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import patch

from app.core.db.schema import init_db
from app.core.reports.generator import generate_report_for_run, generate_report_for_dirs

@pytest.fixture
def temp_db_conn():
    """Create an in-memory SQLite connection with the schema initialised."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()

def test_generate_report_for_run(temp_db_conn, tmp_path):
    # Setup dummy source and target directories and files
    src_dir = tmp_path / "src"
    tgt_dir = tmp_path / "tgt"
    src_dir.mkdir()
    tgt_dir.mkdir()

    # Create original files
    (src_dir / "img1.jpg").write_bytes(b"x" * 1000)
    (src_dir / "img2.png").write_bytes(b"y" * 2000)

    # Create converted files
    (tgt_dir / "img1_magick.webp").write_bytes(b"z" * 400)
    (tgt_dir / "img2_magick.webp").write_bytes(b"w" * 800)

    # Populate DB records for a run
    cur = temp_db_conn.cursor()
    # 1. Insert a batch run record
    cur.execute("""
        INSERT INTO batch_runs (id, source_dir, target_dir, target_format, tool, category, trigger_type, status, total_images)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (1, str(src_dir), str(tgt_dir), "webp", "magick", "general", "manual", "completed", 2))
    
    # 2. Insert summary
    cur.execute("""
        INSERT INTO batch_summary (batch_id, duration_ms, cpu_avg_pct, cpu_peak_pct, ram_peak_mb, yield_mb_sec, savings_pct, success_count, failure_count)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (1, 500.0, 20.0, 50.0, 256.0, 1.5, 60.0, 2, 0))

    # 3. Insert images
    cur.execute("""
        INSERT INTO images (id, filename, category, size_bytes, format)
        VALUES (?, ?, ?, ?, ?)
    """, (1, "img1.jpg", "general", 1000, "jpg"))
    cur.execute("""
        INSERT INTO images (id, filename, category, size_bytes, format)
        VALUES (?, ?, ?, ?, ?)
    """, (2, "img2.png", "general", 2000, "png"))

    # 4. Insert conversions
    cur.execute("""
        INSERT INTO conversions (image_id, format, tool, quality, parameters, duration_ms, output_size_bytes, savings_pct, success)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (1, "webp", "magick", 80.0, "", 200.0, 400, 60.0, 1))
    cur.execute("""
        INSERT INTO conversions (image_id, format, tool, quality, parameters, duration_ms, output_size_bytes, savings_pct, success)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (2, "webp", "magick", 80.0, "", 300.0, 800, 60.0, 1))

    # 5. Insert telemetry
    cur.execute("""
        INSERT INTO batch_telemetry (run_id, timestamp, cpu_pct, ram_mb)
        VALUES (?, datetime('now'), ?, ?)
    """, (1, 15.0, 120.0))
    cur.execute("""
        INSERT INTO batch_telemetry (run_id, timestamp, cpu_pct, ram_mb)
        VALUES (?, datetime('now'), ?, ?)
    """, (1, 25.0, 125.0))

    temp_db_conn.commit()

    output_html = tmp_path / "report.html"

    # Patch get_connection to return our temp DB connection
    with patch("app.core.reports.generator.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = temp_db_conn
        generate_report_for_run(run_id=1, output_path=str(output_html))

    assert output_html.exists()
    content = output_html.read_text(encoding="utf-8")
    assert "Batch Conversion Report - Run #1" in content
    assert "img1.jpg" in content
    assert "img2.png" in content
    assert "magick" in content
    assert "webp" in content
    assert "Space Savings" in content
    assert "CPU Utilization" in content

def test_generate_report_for_dirs(temp_db_conn, tmp_path):
    # Setup dummy source and target directories and files
    src_dir = tmp_path / "src"
    tgt_dir = tmp_path / "tgt"
    src_dir.mkdir()
    tgt_dir.mkdir()

    # Create original files
    (src_dir / "img3.jpg").write_bytes(b"x" * 1000)

    # Create converted files
    (tgt_dir / "img3_magick.webp").write_bytes(b"z" * 400)

    # Populate DB records for conversions (folder-wide report queries this)
    cur = temp_db_conn.cursor()
    cur.execute("""
        INSERT INTO images (id, filename, category, size_bytes, format)
        VALUES (?, ?, ?, ?, ?)
    """, (3, "img3.jpg", "general", 1000, "jpg"))

    cur.execute("""
        INSERT INTO conversions (image_id, format, tool, quality, parameters, duration_ms, output_size_bytes, savings_pct, success)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (3, "webp", "magick", 80.0, "", 150.0, 400, 60.0, 1))

    temp_db_conn.commit()

    output_html = tmp_path / "dirs_report.html"

    # Patch get_connection to return our temp DB connection
    with patch("app.core.reports.generator.get_connection") as mock_get_conn:
        mock_get_conn.return_value.__enter__.return_value = temp_db_conn
        generate_report_for_dirs(source_dir=str(src_dir), target_dir=str(tgt_dir), output_path=str(output_html))

    assert output_html.exists()
    content = output_html.read_text(encoding="utf-8")
    assert "Folder-wide Conversion Summary Report" in content
    assert "img3.jpg" in content
    assert "magick" in content
    assert "webp" in content
    assert "Space Savings" in content
