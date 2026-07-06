# tests/conftest.py
import os
import pytest
from app.core.db.connection import reset_engine_cache

# Force clean loopback/sqlite defaults for testing environment
os.environ["PIXELPIVOT_API_TOKEN"] = ""
os.environ["IS_DOCKER"] = ""
os.environ.pop("PIXELPIVOT_DB_URL", None)


@pytest.fixture(autouse=True)
def clean_engine_cache():
    """Ensure SQLAlchemy engines are disposed between unit tests to prevent cross-contamination."""
    reset_engine_cache()
    yield
    reset_engine_cache()
