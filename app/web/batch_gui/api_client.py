import httpx
from typing import Dict, Any, List, Optional

class APIClient:
    """HTTP client communicating with PixelPivot Batch Engine API."""

    def __init__(self, base_url: str, transport: Optional[httpx.BaseTransport] = None):
        """Initialize client with API base URL.

        Args:
            base_url: Root API URL (e.g., "http://localhost:8000/api/v1").
            transport: Optional pluggable httpx transport.
        """
        self.base_url = base_url
        import os
        headers = {}
        token = os.environ.get("PIXELPIVOT_API_TOKEN")
        if token:
            headers["X-API-Token"] = token
        self._client = httpx.Client(transport=transport, timeout=10.0, headers=headers)

    def start_batch(
        self,
        source_dir: str,
        target_dir: str,
        target_format: List[str],
        tool: List[str],
        category: List[str] = ["general"]
    ) -> Dict[str, Any]:
        """Initiate a batch conversion job.

        Args:
            source_dir: Input directory path.
            target_dir: Output directory path.
            target_format: List of target formats.
            tool: List of conversion tools to use.
            category: List of image categories (default: ["general"]).

        Returns:
            Dict with run_id and status.

        Raises:
            Exception: On API error.
        """
        payload = {
            "source_dir": source_dir,
            "target_dir": target_dir,
            "target_format": target_format,
            "tool": tool,
            "category": category
        }
        response = self._client.post(f"{self.base_url}/batch/start", json=payload)
        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            raise Exception(f"API Error: {detail}")
        return response.json()

    def get_status(self, run_id: int) -> Dict[str, Any]:
        """Query batch job status and summary metrics.

        Args:
            run_id: Unique batch identifier.

        Returns:
            Dict with run_id, status, total_images, created_at, completed_at, summary.

        Raises:
            Exception: On API error.
        """
        response = self._client.get(f"{self.base_url}/batch/status/{run_id}")
        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            raise Exception(f"API Error: {detail}")
        return response.json()

    def get_batch_errors(self, run_id: int) -> List[Dict[str, Any]]:
        """Retrieve error records for a batch job.

        Args:
            run_id: Unique batch identifier.

        Returns:
            List of error dicts.

        Raises:
            Exception: On API error.
        """
        response = self._client.get(f"{self.base_url}/batch/{run_id}/errors")
        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            raise Exception(f"API Error: {detail}")
        return response.json()

    def get_history(self) -> List[Dict[str, Any]]:
        """Retrieve all batch runs and their summaries.

        Returns:
            List of batch run records.

        Raises:
            Exception: On API error.
        """
        response = self._client.get(f"{self.base_url}/batch/history")
        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            raise Exception(f"API Error: {detail}")
        return response.json()

    def register_hot_folder(
        self,
        source_dir: str,
        target_dir: str,
        target_format: str,
        tool: str,
        category: str = "general"
    ) -> Dict[str, Any]:
        """Register a directory for automatic batch processing.

        Args:
            source_dir: Directory to monitor.
            target_dir: Output directory.
            target_format: Target format string.
            tool: Conversion tool name.
            category: Image category (default: "general").

        Returns:
            Dict with watcher_id and status.

        Raises:
            Exception: On API error.
        """
        payload = {
            "source_dir": source_dir,
            "target_dir": target_dir,
            "target_format": target_format,
            "tool": tool,
            "category": category
        }
        response = self._client.post(f"{self.base_url}/hotfolder/register", json=payload)
        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            raise Exception(f"API Error: {detail}")
        return response.json()

    def list_hot_folders(self) -> List[Dict[str, Any]]:
        """Retrieve all active hot folder watchers.

        Returns:
            List of watcher configs.

        Raises:
            Exception: On API error.
        """
        response = self._client.get(f"{self.base_url}/hotfolder/list")
        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            raise Exception(f"API Error: {detail}")
        return response.json()

    def unregister_hot_folder(self, watcher_id: str) -> Dict[str, Any]:
        """Stop and unregister a hot folder watcher.

        Args:
            watcher_id: Unique watcher identifier.

        Returns:
            Dict with status.

        Raises:
            Exception: On API error.
        """
        response = self._client.delete(f"{self.base_url}/hotfolder/{watcher_id}")
        if response.status_code != 200:
            detail = response.json().get("detail", response.text)
            raise Exception(f"API Error: {detail}")
        return response.json()

    def close(self) -> None:
        """Close the underlying HTTPX client."""
        self._client.close()
