# app/tui/render.py
"""Render Rich renderables to ANSI strings for embedding in prompt_toolkit.

prompt_toolkit owns the terminal; we render Rich tables to a string via a
StringIO-backed Console (force_terminal=True) and feed the ANSI text into a
prompt_toolkit window with ANSI() formatting.
"""
from __future__ import annotations

from io import StringIO
from typing import Dict, List

from rich.console import Console
from rich.table import Table

from app.core.toolcheck import ToolStatus


def _render(renderable) -> str:
    buf = StringIO()
    Console(file=buf, force_terminal=True, color_system="standard", width=100).print(renderable)
    return buf.getvalue()


def progress_table(p: Dict) -> str:
    t = Table(title="Batch Progress")
    t.add_column("metric"); t.add_column("value")
    t.add_row("cells", f"{p.get('cells_done', 0)}/{p.get('cells_total', 0)}")
    t.add_row("current", str(p.get("current_cell", "-")))
    t.add_row("ok / fail", f"{p.get('ok', 0)} / {p.get('fail', 0)}")
    t.add_row("cpu %", f"{p.get('cpu_pct', 0):.0f}")
    t.add_row("ram MB", f"{p.get('ram_mb', 0):.0f}")
    return _render(t)


def tools_table(statuses: List[ToolStatus]) -> str:
    t = Table(title="Tools")
    t.add_column("tool"); t.add_column("status"); t.add_column("version/detail")
    for s in statuses:
        t.add_row(s.name, "OK" if s.ok else "DOWN", s.version or s.detail or "")
    return _render(t)


def history_table(runs: List[Dict]) -> str:
    t = Table(title="History")
    for col in ("run_id", "status", "tool", "target_format", "savings_pct"):
        t.add_column(col)
    for r in runs:
        t.add_row(str(r.get("run_id", "")), str(r.get("status", "")),
                  str(r.get("tool", "")), str(r.get("target_format", "")),
                  f"{r.get('savings_pct') or 0:.1f}")
    return _render(t)
