# Air-Gapped Deployment Guide — PixelPivot Batch Engine (Windows)

> For operators deploying the batch engine on a **Windows** host with no
> internet access. Engineering rationale lives in `dev_plan.md`; this file
> is the step-by-step.
>
> **Docker is out of scope this revision.** Use the Windows Sandbox path
> below. The legacy `Dockerfile` still pulls from the network and is not
> air-gap ready.

---

## 1. Prerequisites

### On the **online** workstation (where you build the bundle)
- **Python 3.14** installed and reachable as `python.exe` on `PATH`
  (the wheel pull pins `--abi cp314`; running with 3.12 or 3.13 will
  succeed but `python.exe` itself needs to be a working CPython)
- PowerShell 5.1 or 7
- ~10 GB free disk for the bundle staging area

### On the **air-gapped target**
- Windows 11 with the **Windows Sandbox** feature enabled
  (`Optional Features → Windows Sandbox`)
- No Python, no Node, no ImageMagick install required — the bundle ships
  everything

---

## 2. Build the Bundle (Online Phase)

Three commands from the repo root:

```powershell
# 2a. Refresh wheels — only when scripts/air_gap_deps.txt changes
.\scripts\download_wheels.ps1

# 2b. Assemble the bundle (preflight + stage + manifest, single shot)
.\scripts\build_bundle.ps1

# 2c. Compress for sneakernet
Compress-Archive -Path out\airgap_bundle\* `
                 -DestinationPath out\pixelpivot-airgap-$(Get-Date -f yyyyMMdd).zip
```

### What `build_bundle.ps1` does
1. **Preflight** — refuses to build if `python-3.14.5-embed-amd64\python.exe`
   is missing, the wheel mirror has <30 wheels, or any of `ffmpeg`,
   `ffprobe`, `magick`, `vips`, `node` is missing
2. **Clean** — wipes `out\airgap_bundle\`
3. **Stage** — copies `app/`, `scripts/`, `tools/`, `tests/`,
   `image_samples/`, `python-3.14.5-embed-amd64/`, `vendor/wheels`,
   `vendor/node`, `bin/`, and the project metadata files
4. **Manifest** — runs `scripts\manifest.ps1 -Mode create` so every file
   in the bundle has a SHA256 in `MANIFEST.sha256`

If preflight fails, fix the missing piece and re-run. Don't pass
`-SkipManifest` for production bundles — the manifest is the operator's
only defence against sneakernet corruption.

### What gets EXCLUDED from the bundle (and why)
- `vendor/python/` — stale partial snapshot (65 packages vs 148 in the
  canonical `python-3.14.5-embed-amd64/`)
- `vendor/bin/imagemagick/` — was a byte-identical duplicate of
  `bin/magick/`; deleted from the tree this revision

---

## 3. Transfer

Single ZIP, single hash. Compute on the online side, write the hash on
the physical media label:

```powershell
(Get-FileHash out\pixelpivot-airgap-*.zip -Algorithm SHA256).Hash
```

---

## 4. Install on Target (Offline Phase)

### 4a. Verify and extract

```powershell
# Compare the ZIP hash against the media label first
(Get-FileHash .\pixelpivot-airgap-20260601.zip -Algorithm SHA256).Hash

# Extract — the .wsb expects the project at C:\PixelPivot when the
# sandbox boots, but on the host machine you can extract anywhere.
Expand-Archive .\pixelpivot-airgap-20260601.zip -DestinationPath C:\PixelPivot

# Re-verify every file in the bundle against the embedded manifest
cd C:\PixelPivot
.\scripts\manifest.ps1 -Mode verify
```

**Stop if `manifest.ps1 verify` reports any `MISMATCH` or `MISSING`.**
Do not "fix" by hand — restart the transfer with a known-good bundle.

### 4b. Launch via Windows Sandbox

```powershell
# from C:\PixelPivot on the host
.\PixelPivot.wsb            # double-click also works
```

`PixelPivot.wsb` has `<Networking>Disable</Networking>` — the sandbox
boots with no NIC. Inside the sandbox, `scripts\sandbox_init.ps1` runs
automatically and:

1. Stops leftover `python.exe` / `node.exe` processes (defence against
   Errno 10048 on re-runs)
2. Prepends bundled `ffmpeg`, `magick`, `vips`, `node`, and the embedded
   Python to `PATH`
3. Rewrites `python314._pth` so the embedded interpreter sees
   `site-packages` and the project root
4. Reads `scripts\air_gap_deps.txt` and installs every package from
   `vendor\wheels` with `pip install --no-index --find-links=vendor\wheels
   --no-build-isolation`
5. Launches uvicorn (API, port 8000), the Sharp daemon, then Streamlit
   (GUI, port 8503), each in its own window

Inside the sandbox, open:
- API health: `http://localhost:8000/` → `{"message":"...is running"}`
- GUI: `http://localhost:8503`

### 4c. Streamlit Telemetry & Phone-Home
By default, Streamlit attempts to contact `api.streamlit.io` to check for updates and gather usage statistics. In an air-gapped environment, this causes startup delay while waiting for network timeouts.
The bundle automatically stages a pre-configured `.streamlit/config.toml` file in the directory root containing:
```toml
[browser]
gatherUsageStats = false

[server]
headless = true

[global]
checkUpdate = false
```
This forces Streamlit to run in headless offline mode with all telemetry fully disabled, bypassing any network timeouts.

---

## 5. Smoke Test

Run this immediately after launch — inside the sandbox, against the
running API:

```powershell
cd C:\PixelPivot
.\scripts\smoke_test.ps1
```

Optional flags:

```powershell
# Test more converters
.\scripts\smoke_test.ps1 -Tools magick,vips,ffmpeg

# Switch format
.\scripts\smoke_test.ps1 -Format avif

# More samples, longer timeout
.\scripts\smoke_test.ps1 -SampleCount 20 -TimeoutSec 300
```

Expected output:

```
API up: PixelPivot Batch Engine API is running
Staged 5 samples in C:\PixelPivot\out\smoke\in
--- magick / webp ---
  run_id=1 queued; polling...
  success=5 failure=0
--- vips / webp ---
  run_id=2 queued; polling...
  success=5 failure=0
SMOKE OK
```

Exit codes:
- `0` — pass
- `2` — API not reachable on `localhost:8000`
- `3` — at least one converter reported failures
- `4` — batch did not complete within timeout
- `5` — no sample images found in the bundle

If the smoke test fails, jump to §7 Troubleshooting before reporting an
incident.

---

## 6. Heuristic Table

The shipped `app/core/heuristic_table.json` ships **without priors** —
only a `version` field. On the first batch, the interpolator falls back
to `config.default_quality_for(tool, format)` (conservative).

To seed real priors:

```powershell
# Run after the analytics DB has been populated by at least a few batches
python tools\generate_heuristic_data.py generate-cli `
    --db C:\PixelPivot\data\pixelpivot.db `
    --out C:\PixelPivot\app\core\heuristic_table.json
```

Then restart the API (kill the uvicorn window and re-run
`sandbox_init.ps1`, or restart the sandbox). The interpolator is
content-agnostic, so a `heuristic_table.json` produced on a connected
dev box against similar content is fine to drop in directly.

---

## 7. Troubleshooting

### `pip` says "No matching distribution found for X" during `download_wheels.ps1`
A package has no cp314 wheel on PyPI. Two options:
1. Pin the host to a Python version that does have wheels for it
   (rebuild the bundle on that host)
2. Drop the package from `scripts/air_gap_deps.txt` — only safe if the
   package is in the `# Dev / smoke-test` section and you're shipping
   production-only

### `manifest.ps1 verify` reports `MISMATCH`
The bundle was corrupted in transit or modified after staging. Do **not**
try to patch around it — re-transfer from a known-good source. If
multiple transfers fail, suspect the storage medium (USB drive going
bad).

### Sandbox starts but API never comes up
Check `C:\PixelPivot\pixelpivot.log` inside the sandbox. Most common
cause: `data/` not writeable for the sandbox account. Set
`$env:PIXELPIVOT_DB_PATH = "C:\PixelPivot\sandbox_data\pixelpivot.db"`
near the top of `sandbox_init.ps1` (writeable inside the sandbox,
doesn't fight the host's file locks).

### Sharp converter unavailable
`sandbox_init.ps1` skips the Sharp daemon if `node` isn't on `PATH`.
Inside the sandbox:

```powershell
Get-Command node              # should resolve to C:\PixelPivot\vendor\node\node.exe
node app\scripts\sharp_daemon.js   # should listen on its socket without error
```

If `node.exe` is missing, `vendor/node/` wasn't copied — re-stage from
the online host (`build_bundle.ps1` preflight should have caught this
on the build side).

### Smoke test fails on one tool only (others OK)
Likely a single bad sample tripped a fallback path. Inspect:

```powershell
Get-Content C:\PixelPivot\pixelpivot.log -Tail 100
```

Look for the run_id reported in the smoke output and trace the failed
files. Common cases:
- ImageMagick missing a delegate (HEIC/AVIF without `libheif`) → bundle
  is incomplete
- `ffmpeg` `image2` demuxer rejecting a single corrupt PNG → not fatal;
  the per-file fallback usually picks up the slack

### Converter circuit breaker tripped at startup
An earlier batch hit the fatal-error threshold and the converter was
quarantined. Restart the API (close the uvicorn window and re-run
`sandbox_init.ps1`). If it trips again, the underlying binary is broken
(missing DLL, wrong arch):

```powershell
.\bin\ffmpeg\ffmpeg.exe -version
.\bin\magick\magick.exe -version
.\bin\vips\bin\vips.exe --version
```

Each should print a version banner. If any errors with "missing DLL" or
similar, the bundle was built on a host with a different libc / vcredist
expectation — rebuild on a Windows host that matches the target.

---

## 8. Updating an Existing Deployment

1. On the online workstation, `git pull` and re-run §2 from scratch.
   **Do not** ship incremental diffs — half-updated bundles are the
   worst class of bug.
2. Bump the date suffix on the ZIP filename so operators can tell
   versions apart at a glance.
3. On the target, stop the sandbox (close the sandbox window), back up
   `C:\PixelPivot\data\pixelpivot.db` and any hot-folder root, then
   delete `C:\PixelPivot\` and re-extract.
4. Re-run §5 smoke test before declaring the upgrade complete.

The DB schema is auto-initialised by `init_db()` on startup
(`app/batch_api/main.py`) — no separate migration step today, but
schema changes in future releases will be called out in `CHANGELOG.md`.

---

## 9. Quick Reference

| Need                             | Command                                               |
|----------------------------------|-------------------------------------------------------|
| Build bundle (online host)       | `.\scripts\build_bundle.ps1`                          |
| Refresh wheels (online host)     | `.\scripts\download_wheels.ps1`                       |
| Compress for transfer            | `Compress-Archive -Path out\airgap_bundle\* -DestinationPath out\pixelpivot-airgap-$(Get-Date -f yyyyMMdd).zip` |
| Verify bundle on target          | `.\scripts\manifest.ps1 -Mode verify`                 |
| Launch (Windows Sandbox)         | Double-click `PixelPivot.wsb`                         |
| Smoke test (inside sandbox)      | `.\scripts\smoke_test.ps1`                            |
| Inspect logs                     | `Get-Content C:\PixelPivot\pixelpivot.log -Tail 100`  |
| API health                       | `Invoke-RestMethod http://localhost:8000/`            |
| GUI                              | `http://localhost:8503`                               |
| Regenerate heuristic priors      | `python tools\generate_heuristic_data.py generate-cli --db C:\PixelPivot\data\pixelpivot.db --out C:\PixelPivot\app\core\heuristic_table.json` |
