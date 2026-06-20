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

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self._transport, timeout=10.0)

    def _get(self, path: str) -> Any:
        with self._client() as c:
            r = c.get(f"{self.base_url}{path}")
            r.raise_for_status()
            return r.json()

    def _post(self, path: str, json: Optional[dict] = None) -> Any:
        with self._client() as c:
            r = c.post(f"{self.base_url}{path}", json=json)
            r.raise_for_status()
            return r.json()

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
