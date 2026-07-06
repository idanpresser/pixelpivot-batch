# app/core/api_client.py
"""Shared HTTP client for both Streamlit GUI and TUI talking to the FastAPI backend.

Consolidates API wrappers and manages connection pooling with a single persistent httpx.Client.
"""

from __future__ import annotations
import os
from typing import Any, Dict, List, Optional
import httpx


class APIError(Exception):
    """Exception raised when an API request fails."""
    pass



class ClientCallableWrapper:
    """Wrapper that delegates attribute access to the underlying httpx.Client,
    and is also callable as a method for backward compatibility.
    """
    def __init__(self, get_client_fn):
        self._get_client_fn = get_client_fn

    def __call__(self) -> httpx.Client:
        return self._get_client_fn()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._get_client_fn(), name)


class APIClient:
    """HTTP client communicating with PixelPivot Batch Engine API."""

    def __init__(self, base_url: str, transport: Optional[httpx.BaseTransport] = None):
        """Initialize client with API base URL.

        Args:
            base_url: Root API URL.
            transport: Optional pluggable httpx transport.
        """
        self.base_url = base_url.rstrip("/")
        self._transport = transport
        self._client_instance: Optional[httpx.Client] = None
        self._client = ClientCallableWrapper(self._get_or_create_client)

    @property
    def transport(self) -> Optional[httpx.BaseTransport]:
        return self._transport

    @transport.setter
    def transport(self, value: Optional[httpx.BaseTransport]):
        self._transport = value

    def _get_or_create_client(self) -> httpx.Client:
        if self._client_instance is None:
            headers = {}
            token = os.environ.get("PIXELPIVOT_API_TOKEN")
            if token:
                headers["X-API-Token"] = token
            self._client_instance = httpx.Client(
                transport=self._transport,
                timeout=10.0,
                headers=headers
            )
        return self._client_instance

    def _request(self, method: str, path: str, json: Optional[dict] = None) -> Any:
        c = self._get_or_create_client()
        try:
            r = c.request(method, f"{self.base_url}{path}", json=json)
        except httpx.RequestError as e:
            raise APIError(f"API Connection Error: {e}") from e

        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            raise APIError(f"API Error: {detail}")
        return r.json()

    def _get(self, path: str) -> Any:
        return self._request("GET", path)

    def _post(self, path: str, json: Optional[dict] = None) -> Any:
        return self._request("POST", path, json=json)

    def _delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    def close(self) -> None:
        """Close the underlying HTTPX client."""
        if self._client_instance is not None:
            self._client_instance.close()
            self._client_instance = None

    # --- Batch Operations ---

    def start_batch(
        self,
        source_dir: str,
        target_dir: str,
        target_format: List[str],
        tool: List[str],
        category: List[str] = ["general"]
    ) -> Dict[str, Any]:
        """Initiate a batch conversion job."""
        return self._post("/batch/start", {
            "source_dir": source_dir,
            "target_dir": target_dir,
            "target_format": target_format,
            "tool": tool,
            "category": category
        })

    def get_status(self, run_id: int) -> Dict[str, Any]:
        """Query batch job status and summary metrics."""
        return self._get(f"/batch/status/{run_id}")

    def get_progress(self, run_id: int) -> Dict[str, Any]:
        """Query detailed progress metrics."""
        return self._get(f"/batch/{run_id}/progress")

    def get_history(self) -> List[Dict[str, Any]]:
        """Retrieve all batch runs and their summaries."""
        return self._get("/batch/history")

    def get_errors(self, run_id: int) -> List[Dict[str, Any]]:
        """Retrieve error records for a batch job."""
        return self._get(f"/batch/{run_id}/errors")


    def control(self, run_id: int, action: str) -> Dict[str, Any]:
        """Send a control command (pause/resume) to a batch job."""
        return self._post(f"/batch/{run_id}/control", {"action": action})

    def restart(self, run_id: int) -> Dict[str, Any]:
        """Restart a failed or completed batch job."""
        return self._post(f"/batch/{run_id}/restart")

    # --- Hot Folder Operations ---

    def register_hot_folder(
        self,
        source_dir: str,
        target_dir: str,
        target_format: str,
        tool: str,
        category: str = "general"
    ) -> Dict[str, Any]:
        """Register a directory for automatic batch processing."""
        return self._post("/hotfolder/register", {
            "source_dir": source_dir,
            "target_dir": target_dir,
            "target_format": target_format,
            "tool": tool,
            "category": category
        })

    def list_hot_folders(self) -> List[Dict[str, Any]]:
        """Retrieve all active hot folder watchers."""
        return self._get("/hotfolder/list")

    def unregister_hot_folder(self, watcher_id: str) -> Dict[str, Any]:
        """Stop and unregister a hot folder watcher."""
        return self._delete(f"/hotfolder/{watcher_id}")
