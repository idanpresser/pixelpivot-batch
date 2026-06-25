# app/tui/api_client.py
"""HTTP client for the TUI -> FastAPI backend.

Mirrors app/web/batch_gui/api_client.py but adds the progress, control, and
restart endpoints. A pluggable httpx transport (_transport) keeps it testable
without a live server.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx


class TuiApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._transport: Optional[httpx.BaseTransport] = None
        self._client_instance: Optional[httpx.Client] = None

    def _client(self) -> httpx.Client:
        if self._client_instance is None:
            import os
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

    def _get(self, path: str) -> Any:
        c = self._client()
        r = c.get(f"{self.base_url}{path}")
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json: Optional[dict] = None) -> Any:
        c = self._client()
        r = c.post(f"{self.base_url}{path}", json=json)
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        if self._client_instance is not None:
            self._client_instance.close()
            self._client_instance = None

    def start_batch(self, source_dir: str, target_dir: str,
                    target_format: List[str], tool: List[str],
                    category: List[str]) -> Dict[str, Any]:
        return self._post("/batch/start", {
            "source_dir": source_dir, "target_dir": target_dir,
            "target_format": target_format, "tool": tool, "category": category,
        })

    def get_status(self, run_id: int) -> Dict[str, Any]:
        return self._get(f"/batch/status/{run_id}")

    def get_progress(self, run_id: int) -> Dict[str, Any]:
        return self._get(f"/batch/{run_id}/progress")

    def get_history(self) -> List[Dict[str, Any]]:
        return self._get("/batch/history")

    def get_errors(self, run_id: int) -> List[Dict[str, Any]]:
        return self._get(f"/batch/{run_id}/errors")

    def control(self, run_id: int, action: str) -> Dict[str, Any]:
        return self._post(f"/batch/{run_id}/control", {"action": action})

    def restart(self, run_id: int) -> Dict[str, Any]:
        return self._post(f"/batch/{run_id}/restart")
