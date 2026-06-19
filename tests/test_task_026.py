"""Task 026 - one canonical dep list, two consumers.

Today scripts/download_wheels.ps1 and scripts/sandbox_init.ps1 each hardcode
their own copy of the install list. A teammate adding a dep to one but not
the other ships a broken air-gap closure. Fix: extract the list to a single
text file that both scripts read.
"""
from __future__ import annotations
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
SCRIPTS = PROJ / "scripts"
SHARED = SCRIPTS / "air_gap_deps.txt"
DOWNLOAD = SCRIPTS / "download_wheels.ps1"
SANDBOX = SCRIPTS / "sandbox_init.ps1"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _read_dep_list() -> list[str]:
    """Parse the shared dep list, stripping blanks and # comments."""
    return [
        line.strip()
        for line in SHARED.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_shared_dep_list_file_exists() -> None:
    assert SHARED.exists(), f"missing {SHARED.relative_to(PROJ)}"


def test_shared_dep_list_has_canonical_packages() -> None:
    """The shared list must include the deps pyproject.toml requires."""
    deps = {d.lower() for d in _read_dep_list()}
    must_have = {
        "fastapi",
        "uvicorn[standard]",
        "pydantic",
        "watchdog",
        # streamlit intentionally excluded — GUI is an optional [gui] extra,
        # not part of the headless air-gap bundle (bead y0z).
        "pillow",
        "pillow-heif",
        "pyvips",
        "pandas",
        "psutil",
        "nvidia-ml-py",
    }
    missing = {d for d in must_have if d not in deps}
    assert not missing, f"shared dep list missing: {missing}"


def test_both_scripts_read_the_shared_list() -> None:
    """Neither script may carry a hardcoded inline $deps = @(...) array."""
    for path in (DOWNLOAD, SANDBOX):
        text = _read(path)
        assert "air_gap_deps.txt" in text, (
            f"{path.name} must read deps from scripts/air_gap_deps.txt"
        )


def test_no_inline_deps_assignment_in_scripts() -> None:
    """Catch any future regression that re-inlines the list as a literal."""
    import re
    # Match either $deps = @( ... "name" ... ) OR $deps = "name", "name", ...
    pat_array = re.compile(r"\$deps\s*=\s*@\(\s*\"")
    pat_csv = re.compile(r'\$deps\s*=\s*"[a-zA-Z][^"\n]*"\s*,')
    for path in (DOWNLOAD, SANDBOX):
        text = _read(path)
        offenders = []
        if pat_array.search(text):
            offenders.append(f"{path.name}: $deps = @( ... ) literal array")
        if pat_csv.search(text):
            offenders.append(f"{path.name}: $deps = csv literal")
        assert not offenders, (
            "inline dep arrays must be replaced by Get-Content on the "
            f"shared file: {offenders}"
        )
