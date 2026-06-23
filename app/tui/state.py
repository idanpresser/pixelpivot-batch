# app/tui/state.py
"""Pure UI state for the TUI: selections and the submit payload builder.

No prompt_toolkit imports here — keep decision logic unit-testable in isolation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

TABS = ["submit", "telemetry", "history", "tools", "settings"]
FORMATS = ["webp", "avif", "jxl"]


@dataclass
class UiState:
    enabled_tools: List[str] = field(default_factory=lambda: ["magick", "ffmpeg", "vips", "sharp"])
    selected_tools: List[str] = field(default_factory=list)
    selected_formats: List[str] = field(default_factory=list)
    source_dir: str = ""
    target_dir: str = ""
    category: str = "general"
    active_tab: str = "submit"
    active_run_id: int | None = None
    settings: dict = field(default_factory=dict)
    toast: str | None = None
    progress_cache: dict = field(default_factory=dict)
    run_finalized: bool = False
    final_status: dict | None = None

    def toggle_tool(self, tool: str) -> None:
        if tool not in self.enabled_tools:
            return
        if tool in self.selected_tools:
            self.selected_tools.remove(tool)
        else:
            self.selected_tools.append(tool)

    def toggle_format(self, fmt: str) -> None:
        if fmt not in FORMATS:
            return
        if fmt in self.selected_formats:
            self.selected_formats.remove(fmt)
        else:
            self.selected_formats.append(fmt)


def build_batch_payload(s: UiState) -> Dict[str, object]:
    """Validate selections and produce a /batch/start payload."""
    if not s.source_dir or not s.target_dir:
        raise ValueError("source_dir and target_dir are required")
    if not s.selected_tools:
        raise ValueError("select at least one tool")
    if not s.selected_formats:
        raise ValueError("select at least one format")
    return {
        "source_dir": s.source_dir,
        "target_dir": s.target_dir,
        "tool": list(s.selected_tools),
        "target_format": list(s.selected_formats),
        "category": [s.category or "general"],
    }
