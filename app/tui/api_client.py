# app/tui/api_client.py
"""HTTP client for the TUI -> FastAPI backend.

Delegates to the shared APIClient in app.core.api_client.
"""
from app.core.api_client import APIClient as TuiApiClient
