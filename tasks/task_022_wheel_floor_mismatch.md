# Task 022 — Reconcile the pyproject Python floor with the vendored wheels

**Severity:** HIGH (air-gap deploy lies about the supported Python version)
**Feature:** Air-gap dependency closure (checklist item 1, plus G4)
**Air-gap relevance:** **Critical.** `scripts\sandbox_init.ps1` performs an
offline `pip install --no-index --find-links="vendor\wheels"` install. If the
embedded Python distro inside the Sandbox is not the exact ABI that the
vendored native wheels were built for, *every* native dep fails to install.

## Reproduction (the breadcrumb)

On the host:
```
PS> python -c "import sys; print(sys.version)"
3.14.4 ...

PS> Get-ChildItem F:\DEV\PixelPivot_202605\pixelpivot_batch\vendor\wheels |
    Where-Object Name -match 'cp3'
```
Observe that **every native wheel** in `vendor\wheels` is `cp314-*` only:
```
cffi-2.0.0-cp314-cp314-win_amd64.whl
charset_normalizer-3.4.7-cp314-cp314-win_amd64.whl
httptools-0.7.1-cp314-cp314-win_amd64.whl
markupsafe-3.0.3-cp314-cp314-win_amd64.whl
numpy-2.4.6-cp314-cp314-win_amd64.whl
pandas-3.0.3-cp314-cp314-win_amd64.whl
pillow-12.2.0-cp314-cp314-win_amd64.whl
pillow_heif-1.3.0-cp314-cp314-win_amd64.whl
pyarrow-24.0.0-cp314-cp314-win_amd64.whl
pydantic_core-2.46.4-cp314-cp314-win_amd64.whl
pyyaml-6.0.3-cp314-cp314-win_amd64.whl
rpds_py-0.30.0-cp314-cp314-win_amd64.whl
scikit_image-0.26.0-cp314-cp314-win_amd64.whl
scipy-1.17.1-cp314-cp314-win_amd64.whl
tifffile-2026.5.15-py3-none-any.whl   (universal)
watchfiles-1.2.0-cp314-cp314-win_amd64.whl
websockets-16.0-cp314-cp314-win_amd64.whl
```
Compare with `pyproject.toml:6`: `requires-python = ">=3.12"`.

Now simulate an air-gap deploy onto a 3.12 sandbox image:
```
py -3.12 -m pip install --no-index --find-links="vendor\wheels" --no-build-isolation `
   fastapi uvicorn[standard] httpx pydantic Pillow pillow-heif pyvips
# observe: ERROR: Could not find a version that satisfies the requirement
# pydantic_core (none of the wheels match cp312-cp312-win_amd64)
```

`sandbox_init.ps1:14` hardcodes `python-3.14.5-embed-amd64` -- so on the
Sandbox the install works *only* because the Python version was secretly
pinned to 3.14 in the launcher. The `pyproject.toml` claim of `>=3.12` is
unbacked by closure.

## Root cause (from the code, not a doc)

Two coupled declarations are out of sync:

- `pyproject.toml:6` declares `requires-python = ">=3.12"`.
- `scripts\sandbox_init.ps1:14` requires a 3.14 embedded distro.
- `scripts\download_wheels.ps1:37-39` downloads wheels using whatever
  `python.exe` is on the host PATH (no `--python-version=3.14` flag, no
  `--platform=win_amd64`, no `--abi=cp314`).

Net effect: the closure is correct only when the host that ran
`download_wheels.ps1` was Python 3.14 on Windows AMD64. There's no
machine-readable invariant pinning this; a teammate running on Python 3.12
or Linux would produce a broken `vendor\wheels` directory and no error would
surface until deploy.

`CLAUDE.md` (you may *not* trust this -- but the inspection prompt's
"Python 3.12 <-> 3.14 parity" call-out is a direct symptom of this drift).

## Required behavior

Make the supported-Python claim load-bearing. The minimal options are:

1. **Tighten the floor to `>=3.14`** in `pyproject.toml`. Update any
   `>=3.12` mention elsewhere. Add a runtime guard in
   `app/batch_api/main.py` lifespan: `if sys.version_info < (3, 14):
   raise RuntimeError(...)` (so an accidental 3.12 boot fails loudly
   instead of producing the cryptic `pip` error during install).
2. **Bundle 3.12 wheels too** by changing `download_wheels.ps1` to download
   for both `--python-version=3.12` *and* `--python-version=3.14` (and any
   target ABI tags). Double the size of `vendor\wheels`. Only worth it if a
   second deploy target *actually* runs 3.12.

Pick option 1 unless option 2 is justified by a real second deploy.

Additionally, `download_wheels.ps1` must produce a deterministic closure for
the chosen target. Add explicit flags:

```
& $PythonExe -m pip download $deps --dest $WheelsDir `
    --only-binary=:all: `
    --python-version=314 --platform=win_amd64 --abi=cp314 --implementation=cp
```

This guarantees the host's local Python version is irrelevant.

## TDD plan

RED -- `tests/test_task_022.py` (ASCII only):

1. Parse `pyproject.toml` and read `requires-python`.
2. Glob `vendor/wheels/*.whl`; for every wheel whose name contains a `cp3xx`
   ABI tag, parse the tag set with `packaging.utils.parse_wheel_filename`.
3. Assert: the minimum Python version in `requires-python` is supported by
   at least one wheel matching `(impl='cp', python_version <= floor)` for
   every package on the install list. Today this fails because no wheel
   matches `cp312`.
4. Negative test on `download_wheels.ps1`: parse the script text and assert
   it contains `--python-version=` and `--platform=` flags (or wraps a
   pinned Python version). Fails today.

GREEN -- minimal change:

- Edit `pyproject.toml:6` -> `requires-python = ">=3.14"`.
- Add the lifespan-time `sys.version_info` guard in `app/batch_api/main.py`.
- Edit `scripts/download_wheels.ps1` to pass `--python-version=314
  --platform=win_amd64 --abi=cp314 --implementation=cp` to both `pip
  download` calls.
- Re-run the test; assert green.

## Acceptance criteria

- [ ] `pyproject.toml`'s `requires-python` matches the actual closure in
      `vendor/wheels/`.
- [ ] `app/batch_api/main.py` lifespan raises clearly if launched on an
      unsupported Python (caught and surfaced; do not crash the lifespan
      silently).
- [ ] `scripts/download_wheels.ps1` includes explicit `--python-version`,
      `--platform`, `--abi`, `--implementation` flags so the closure is
      reproducible regardless of host Python.
- [ ] The wheel-closure test passes (no missing ABI matches for the declared
      floor).
- [ ] Full `pytest` suite green.
- [ ] Any new tunable in `app/core/config.py`.
- [ ] `convert_batch()` return shape unchanged.
- [ ] No `int()` cast of `quality` inside converter code.
- [ ] ASCII-only test code/messages.

## Constraints for the implementer (Sonnet)

TDD only (red before green, paste failing output first). No destructive ops,
no `git push` / force / amend / `--no-verify`. Fix exactly this defect -- no
drive-by refactors. Behavior identical on Python 3.12 and 3.14 -- but if you
chose option 1, the test is "raises clear RuntimeError on 3.12, runs on 3.14".
