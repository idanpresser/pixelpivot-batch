import pytest
from pathlib import Path
import sqlite3
from PIL import Image
from app.core.db.repositories.images import register_image
from app.core.db.schema import init_db

@pytest.fixture
def db_conn(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    # Configure connection row_factory as connection.py does
    conn.row_factory = sqlite3.Row
    init_db(conn)
    yield conn
    conn.close()

def test_truncated_image_is_marked_corrupt(db_conn, tmp_path):
    """
    Verify that a truncated image is correctly detected as corrupt.
    """
    # 1. Create a valid PNG image
    valid_path = tmp_path / "valid.png"
    img = Image.new("RGB", (100, 100), color="red")
    img.save(str(valid_path), "PNG")
    
    # Register valid image
    register_image(db_conn, str(valid_path), "general")
    
    # Check that it is NOT corrupt
    cur = db_conn.cursor()
    cur.execute("SELECT is_corrupt FROM images WHERE filename = 'valid.png'")
    row = cur.fetchone()
    assert row[0] == 0
    
    # 2. Create a truncated image (first 100 bytes of the valid PNG)
    truncated_path = tmp_path / "truncated.png"
    valid_bytes = valid_path.read_bytes()
    # Write only a fraction of the bytes (enough to open the header but fail decoding)
    truncated_path.write_bytes(valid_bytes[:200])
    
    # Register truncated image
    register_image(db_conn, str(truncated_path), "general")
    
    # Check that it IS marked corrupt
    cur.execute("SELECT is_corrupt FROM images WHERE filename = 'truncated.png'")
    row = cur.fetchone()
    assert row[0] == 1
