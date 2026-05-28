# Task 007 — Harden path handling in `_resolve_path`

**Severity:** LOW-MEDIUM (security posture for a local tool; correctness for empty input)
**Phase:** II — path sanitization
**Confidence:** Confirmed by runtime probe

## Problem

`models.py:16-20`:

```python
def _resolve_path(v: str) -> str:
    try:
        return str(Path(v).resolve())
    except (ValueError, OSError) as e:
        raise ValueError(f"Invalid path: {e}")
```

`Path.resolve()` **normalizes** but does not **contain**. Probes:

```text
_resolve_path('../../etc/passwd') -> 'F:\\DEV\\etc\\passwd'   # escaped the project root
_resolve_path('')                 -> 'F:\\DEV\\PixelPivot_202605\\pixelpivot_batch'  # CWD, no error
```

So:
- Any relative path with `..` resolves against CWD and can point **anywhere on the
  volume** — there is no allowed-root sandbox.
- An **empty string silently becomes the CWD** instead of being rejected (interacts with
  [task_004](task_004_backend_matrix_validation.md): empty GUI fields are blocked client-side
  but a direct API call slips through).
- Windows reserved device names (`CON`, `NUL`, `PRN`) and trailing dots/spaces are not
  rejected; they currently resolve to odd paths and fail later inside `mkdir`/`iterdir`
  in a confusing way.

For a single-user local conversion tool this is not a remote-exploit vector, but it is a
latent footgun (e.g. a hot folder writing outside its intended tree) and worth hardening.

## Fix (choose per threat model)

**Minimum (recommended):** reject empty/whitespace and obviously invalid input.

```python
def _resolve_path(v: str) -> str:
    if not v or not v.strip():
        raise ValueError("Path must not be empty.")
    try:
        return str(Path(v).resolve(strict=False))
    except (ValueError, OSError) as e:
        raise ValueError(f"Invalid path: {e}")
```

**Optional containment:** if an allowed-root env var is configured
(e.g. `PIXELPIVOT_ALLOWED_ROOT`), require the resolved path to be a descendant:

```python
root = os.environ.get("PIXELPIVOT_ALLOWED_ROOT")
if root:
    base = Path(root).resolve()
    resolved = Path(v).resolve()
    if base not in resolved.parents and resolved != base:
        raise ValueError("Path escapes the allowed root.")
```

Keep containment **opt-in** so existing local workflows (arbitrary absolute paths) are not
broken — this matches the route docstring "Triggers an arbitrary path batch job".

## Acceptance criteria
- Empty / whitespace-only `source_dir` or `target_dir` returns 422.
- With `PIXELPIVOT_ALLOWED_ROOT` set, a `../` traversal outside the root is rejected.
- With the env var unset, behavior is unchanged (arbitrary absolute paths still allowed).
- Tests use `tempfile` dirs only; no writes outside them (ASCII output only).
