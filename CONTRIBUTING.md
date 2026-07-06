# Contributing to PixelPivot Batch Engine

Thanks for taking the time to contribute. This guide covers the practical
workflow: how to set up a local environment, how the test and issue
tooling work, and what we look for in pull requests.

By participating, you agree to abide by the project's [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Quick start

```bash
# 1. Clone the repo (external contributors: fork first, then clone your fork)
git clone https://github.com/idanpresser/pixelpivot-batch.git
cd pixelpivot-batch

# 2. Create a Python 3.14+ virtualenv and install dev extras
python -m venv .venv
.venv\Scripts\Activate.ps1        # PowerShell
# source .venv/bin/activate       # bash / zsh
pip install -e ".[dev]"

# 3. Run the tests
pytest
```

The repo expects external converter binaries (`ffmpeg`, `magick`, `vips`)
to be discoverable on `PATH` or in a local `bin/` directory. See the
[README](README.md#installation) for the air-gap bundle layout.

---

## Project layout

```
app/
├── batch_api/        FastAPI service: orchestrator + hot-folder watcher
├── core/
│   ├── converters/   BaseConverter + 4 backends (magick/ffmpeg/vips/sharp)
│   ├── db/           SQLite schema + repository layer
│   ├── ffmpeg/       Subprocess plumbing + error classification
│   └── heuristic*    Log-linear quality interpolator + curve fitter
└── web/batch_gui/    Streamlit GUI (REST client only)
tests/                Pytest suites (hardening + integration)
tools/                CLI helpers (heuristic table generator, stress harness)
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the canonical
architecture notes — every design constraint there is load-bearing.

---

## Concurrency & Converter Safety

The converter layer is **not thread-safe** for concurrent batch execution. Before writing concurrency-related code, understand the current limitations:

### Known Issues (E12 Runtime Audit)

- **Breaker state corruption** (`bd-qk1.1`): `BaseConverter._mark_failure()` and `_reset_failures()` mutate `state["consecutive_failures"]` outside the lock. Under ThreadPoolExecutor batch workloads, counter mutations race and produce lost updates.
  
- **Cross-run breaker interference** (`bd-qk1.3`): `_reset_failures()` wipes the global `None`-keyed breaker state, causing concurrent batches to share breaker state. Runs are not isolated.

- **Process-global calibration flag** (`bd-qk1.2`): `config.CALIBRATION_ENABLED` is set by a worker thread and never reset. Once any calibration run executes, all subsequent normal batches silently write calibration rows.

### Do Not (Until E12 is Resolved)

- **Do not** spawn multiple `ThreadPoolExecutor` pools on the same converter singletons
- **Do not** run multiple concurrent batches with the same orchestrator instance
- **Do not** assume `config.CALIBRATION_ENABLED` is per-batch; it is process-global

### Recommended Practice

- Use the DB-poll queue manager (default) to serialize batches
- Keep `PIXELPIVOT_MAX_CONCURRENT_BATCHES = 1`
- Restart the API process after calibration
- See [E12 beads](https://github.com/gastownhall/beads) for fix status and detailed reproduction steps

---

## Development workflow

### 1. Pick or file an issue

This project uses **[beads](https://github.com/gastownhall/beads)** (`bd`)
for issue tracking. Issues live in a local Dolt DB and sync via the git
remote.

```bash
bd ready              # show issues with no blockers
bd show <id>          # full issue detail
bd update <id> --claim
```

If you do not have `bd` installed, file a regular GitHub issue and a
maintainer will mirror it into the beads database.

### 2. Branch off `main`

```bash
git checkout -b feat/<short-name>
```

### 3. Write tests first

The project follows test-driven development for behavior changes. New
features and bug fixes both start with a failing test in `tests/`. The
hardening suites (`tests/test_phase{1,2,3,4}_hardening.py`) are the
reference for the bar we expect on subprocess/memory/database changes.

```bash
pytest tests/test_<area>.py    # focused
pytest -k "<keyword>"           # by keyword
pytest                          # full suite
```

### 4. Implement and self-review

- **Style**: 4-space indent, type hints on public functions, Google-style
  docstrings.
- **Quality is tool-and-format-native** — do not cast to `int` inside
  converters and do not assume a single normalized scale. See
  `config.default_quality_for` / `DEFAULT_QUALITY_BY_TOOL_FORMAT`.
- **`convert_batch()` returns a dict** with keys `success_count`,
  `failure_count`, `duration_ms`, `errors` — not a list.
- **No icons or emojis in code or tests** — Python's default stdout
  encoding on Windows does not always handle them cleanly.
- Subprocess calls should flow through `BaseConverter._run_subprocess`
  to inherit telemetry and circuit-breaker behavior.

### 5. Commit and open a PR

Commit messages follow a lowercase prefix style (`fix:`, `feat:`,
`refactor:`, `docs:`, `test:`, `chore:`). Reference the beads issue ID
or GitHub issue number in the body.

PRs should:

- Pass the full `pytest` suite locally.
- Update `CHANGELOG.md` under `[Unreleased]` with a short bullet.
- Touch only files relevant to the change — no incidental reformatting.
- Include a brief description of the design choice in the PR body when
  the diff is non-obvious.

---

## What we look for in PRs

- **Behavior changes ship with tests.** Bug fixes ship with a regression
  test reproducing the bug.
- **Air-gap friendliness.** The project is designed to run on offline
  Windows hosts. Avoid introducing network calls in code paths that the
  air-gap deployment exercises (Streamlit is allowed to fetch local
  assets only; remote font/CDN dependencies are not acceptable in the
  GUI).
- **Resource bounding.** Long-lived queues, deques, and pools must have
  explicit caps. The hardening pass capped telemetry at 2000 samples and
  FFmpeg progress at 1000 — follow that pattern.
- **Backwards-compatible database migrations.** The SQLite schema is
  upgraded in `app/core/db/schema.py`. Adding columns is fine; renaming
  or dropping requires a migration step and a `CHANGELOG` note.

---

## Reporting bugs

Please include:

1. PixelPivot version (`git rev-parse HEAD` or release tag).
2. Operating system and Python version.
3. The minimal command or API call that reproduces the bug.
4. Relevant excerpts from `pixelpivot.log` (the project rotates logs to
   `pixelpivot.log.1`, etc.).

Sensitive paths or filenames can be redacted; the relevant signal is
usually the converter output and the surrounding orchestrator log lines.

---

## Security

Please do **not** open public issues for security-sensitive reports.
Instead, email idanpresser@gmail.com with the details and a suggested
fix if you have one. We will acknowledge receipt within a few business
days.

---

Thanks again for contributing.
