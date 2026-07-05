r"""Task 023 - sandbox_init.ps1 must boot Sharp without network egress.

The sandbox runs with <Networking>Disable</Networking>, so any `npm install`
command without an offline-class flag will hit registry.npmjs.org and fail.
The fix is to use `npm start` directly when `node_modules\sharp` is already
mapped into the sandbox (which it is, via the .wsb folder mapping), and to
log an actionable error otherwise.
"""
from __future__ import annotations
import re
from pathlib import Path

import pytest

PROJ = Path(__file__).resolve().parent.parent
SCRIPT = PROJ / "scripts" / "sandbox_init.ps1"


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_no_bare_npm_install() -> None:
    """`npm install` without an offline flag requires network egress.

    Only flags lines that EXECUTE the command. Help text inside Write-Host
    quoted strings is allowed (the operator needs to see what to run).
    """
    text = _script_text()
    for line in text.splitlines():
        stripped = line.split("#", 1)[0]  # strip PS line comments
        if not re.search(r"\bnpm\s+install\b", stripped):
            continue
        # Skip if this is documentation inside a Write-Host call.
        if re.search(r"\bWrite-Host\b", stripped):
            continue
        # Allow if the same line carries an offline-class flag.
        if re.search(r"--(offline|prefer-offline|offline-mirror)\b", stripped):
            continue
        pytest.fail(
            f"sandbox_init.ps1 contains a bare `npm install` (no offline flag): {line.strip()!r}"
        )


def test_starts_sharp_via_npm_start_when_node_modules_present() -> None:
    """When the vendored node_modules\\sharp is mapped in, just run `npm start`."""
    text = _script_text()
    # Must reference the vendored sharp module path
    assert re.search(r"node_modules[\\/]sharp", text), (
        "sandbox_init.ps1 must check for the vendored node_modules\\sharp path"
    )
    # Must call `npm start` (or node services/sharp-daemon/sharp_daemon.js) on the
    # offline-mapped install
    assert re.search(r"\bnpm\s+start\b|node\s+services[\\/]sharp-daemon[\\/]sharp_daemon\.js", text), (
        "sandbox_init.ps1 must boot Sharp via `npm start` or the daemon directly"
    )


def test_missing_node_modules_path_is_actionable() -> None:
    """If the vendored node_modules is missing, log an actionable warning."""
    text = _script_text()
    # Substring assertion: the operator needs to know how to fix it on the host.
    # Allow either of these phrasings.
    has_actionable = any(
        phrase in text
        for phrase in (
            "npm ci",
            "node_modules\\sharp not",
            "node_modules/sharp not",
            "before launching the sandbox",
            "Run `npm install`",
            "run `npm install`",
        )
    )
    assert has_actionable, (
        "sandbox_init.ps1 must include an actionable message when node_modules is missing"
    )
