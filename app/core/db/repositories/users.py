"""Repository for users table persistence and authentication.

Handles user retrieval, creation, and counting for authentication and
account management.
"""

import sqlite3
from typing import Optional, Dict, Any
from ..connection import get_connection
from ...logger import get_logger

log = get_logger(__name__)

def get_user(username: str) -> Optional[Dict[str, Any]]:
    """Fetch a single user by username.

    Args:
        username: users.username to retrieve.

    Returns:
        dict with columns: username, password_hash, is_active; or None
        if not found. Best-effort on errors (logged, None returned).
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT username, password_hash, is_active FROM users WHERE username = ?",
                    (username,)
                )
                row = cur.fetchone()
                return dict(row) if row else None
            finally:
                cur.close()
    except Exception as e:
        log.error(f"Error fetching user '{username}': {e}")
        return None

def create_user(username: str, password_hash: str, full_name: str = None) -> bool:
    """Insert a new users row.

    Uses SQLite UPSERT ON CONFLICT DO NOTHING to allow idempotent creation.

    Args:
        username: Unique username identifier.
        password_hash: Hashed password (not plaintext).
        full_name: Optional full name of the user.

    Returns:
        bool: True on success, False on error (logged).
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute(
                    "INSERT INTO users (username, password_hash, full_name) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                    (username, password_hash, full_name)
                )
            finally:
                cur.close()
            conn.commit()
            return True
    except Exception as e:
        log.error(f"Error creating user '{username}': {e}")
        return False

def count_users() -> int:
    """Return the total number of rows in the users table.

    Returns:
        int: User count. Best-effort on errors (logged, 0 returned).
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            try:
                cur.execute("SELECT COUNT(*) AS cnt FROM users")
                row = cur.fetchone()
                return row["cnt"] if row else 0
            finally:
                cur.close()
    except Exception as e:
        log.error(f"Error counting users: {e}")
        return 0
