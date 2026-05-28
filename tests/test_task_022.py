"""Task 022 - pyproject.toml requires-python must match vendored wheel ABIs.

Today vendor/wheels/ contains only cp314 native wheels but pyproject claims
`>=3.12`. On a 3.12 sandbox `pip install --no-index` fails on every native
dep. The fix tightens the floor to >=3.14 (matches the wheels and the
embedded distro `python-3.14.5-embed-amd64` referenced by sandbox_init.ps1)
and adds explicit pin flags to download_wheels.ps1 so the closure is
reproducible regardless of host Python.
"""
from __future__ import annotations
import re
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _parse_requires_python() -> str:
    text = _read(PROJ / "pyproject.toml")
    m = re.search(r'requires-python\s*=\s*"([^"]+)"', text)
    assert m, "pyproject.toml missing requires-python"
    return m.group(1)


def _native_wheel_abis() -> set[str]:
    """Return the set of cpython ABI tags for native wheels in vendor/wheels."""
    abis: set[str] = set()
    for whl in (PROJ / "vendor" / "wheels").glob("*.whl"):
        # Wheel filename: name-version-pyTag-abiTag-platTag.whl
        # We care about wheels with a CPython ABI tag (cp3XX) - those are
        # the native ones that pip cannot use across Python versions.
        parts = whl.stem.split("-")
        if len(parts) < 3:
            continue
        abi = parts[-2]
        if re.fullmatch(r"cp3\d{2}", abi):
            abis.add(abi)
    return abis


def test_requires_python_matches_vendored_native_wheels() -> None:
    """A 3.12 floor with no cp312 wheels is a deceptive closure."""
    abis = _native_wheel_abis()
    assert abis, "vendor/wheels has no cpython-ABI wheels - cannot verify floor"
    requires = _parse_requires_python()
    # Extract the floor version, e.g. ">=3.14" -> 14
    m = re.search(r">=\s*3\.(\d+)", requires)
    assert m, f"requires-python {requires!r} not in >=3.X form"
    floor_minor = int(m.group(1))
    # Every cp ABI in vendor must be >= the declared floor.
    cp_minors = {int(abi[3:]) for abi in abis}
    min_vendored = min(cp_minors)
    assert floor_minor >= min_vendored, (
        f"requires-python={requires} but vendor wheels only support cp3{min_vendored}+. "
        f"Tighten the floor or vendor older-Python wheels."
    )


def test_download_wheels_script_pins_target_python() -> None:
    """Closure must be reproducible regardless of host Python version."""
    text = _read(PROJ / "scripts" / "download_wheels.ps1")
    assert "--python-version" in text, (
        "download_wheels.ps1 must pin --python-version so closure is host-independent"
    )
    assert "--platform" in text, (
        "download_wheels.ps1 must pin --platform=win_amd64"
    )
    assert "--abi" in text, (
        "download_wheels.ps1 must pin --abi=cp3XX"
    )
    assert "--implementation" in text, (
        "download_wheels.ps1 must pin --implementation=cp"
    )


def test_main_lifespan_guards_python_version() -> None:
    """Air-gap deploy onto a wrong Python should fail loudly at lifespan, not
    cryptically at import."""
    text = _read(PROJ / "app" / "batch_api" / "main.py")
    assert "sys.version_info" in text, (
        "main.py lifespan must check sys.version_info against the declared floor"
    )
