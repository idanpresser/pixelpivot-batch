# Task 023 — Replace `npm install` with an offline-safe sharp boot in sandbox_init

**Severity:** CRITICAL (air-gap deploy; without this, Sharp converter never
starts on the documented launch path on a `<Networking>Disable>` sandbox.)
**Feature:** C4 SharpConverter degradation + air-gap launch (G4)
**Air-gap relevance:** **Yes — this is the air-gap defect.** The reference
launcher in `PixelPivot.wsb` disables networking; the launcher script then
unconditionally executes `npm install` which expects to talk to
https://registry.npmjs.org.

## Reproduction (the breadcrumb)

Inside a Windows Sandbox where `<Networking>Disable</Networking>` is set in
`PixelPivot.wsb`, run `scripts\sandbox_init.ps1`. Observe at step `[4/4]`:
```
  -> Node.js found. Starting Sharp Daemon...
# new powershell window:
cd C:\PixelPivot; npm install; npm start
# npm install:
npm error code ENOTFOUND
npm error errno ENOTFOUND
npm error network request to https://registry.npmjs.org/sharp failed
```

Even if `node_modules\sharp` is already present on disk (it currently is in
the dev repo), the unconditional `npm install` still attempts a registry
metadata fetch and on `<Networking>Disable>` fails with `ENOTFOUND` -- the
subsequent `npm start` never runs, the daemon never binds 127.0.0.1:8765,
and `SharpConverter.convert(...)` falls through to a connection error on
every call.

This is the air-gap-case for feature C4, and it is currently broken on
the documented happy path.

## Root cause (from the code, not a doc)

`scripts\sandbox_init.ps1:71-76`:
```powershell
if (Get-Command node -ErrorAction SilentlyContinue) {
    Write-Host "  -> Node.js found. Starting Sharp Daemon..."
    Start-Process powershell.exe -ArgumentList "-NoExit", "-Command",
        "cd $ProjectRoot; npm install; npm start" -WindowStyle Minimized
}
```

`npm install` (no flags) requires network access. `npm install --offline`
plus a pre-populated `node_modules` would work; `npm ci --offline` is the
formal "reproducible offline install" verb but also expects a `package-lock.json`
and a populated cache.

The dev repo *does* contain `node_modules\sharp\` (verified via
`Glob node_modules/sharp/package.json`), and `PixelPivot.wsb` maps the
project folder into `C:\PixelPivot` -- so `node_modules` arrives in the
sandbox preinstalled. The ONLY missing piece is that the init script tries
to reinstall instead of trusting what's mapped.

## Required behavior

Sharp daemon boots from the pre-mapped `node_modules` without any network
request. On a sandbox with no `node_modules`, the script should fail loudly
with an actionable message ("vendored node_modules/ missing -- run `npm ci`
on the host before launching the sandbox") -- not silently fall through with
networking attempts that will fail anyway.

## TDD plan

RED -- `tests/test_task_023.py` (ASCII only):

1. Read `scripts\sandbox_init.ps1` as text.
2. Assert that the script:
   - Does NOT contain a bare `npm install` (regex: `\bnpm\s+install\b`
     without an `--offline`-class flag).
   - Either calls `npm start` directly when `node_modules\sharp` exists, OR
     calls `npm ci --offline` / `npm install --offline --no-audit
     --no-fund` / equivalent.
   - Contains an actionable error path when `node_modules\sharp` is missing
     (substring assertion).
3. Optional integration check: a small PowerShell-friendly fixture that
   stubs `Get-Command node` to fail and verifies the script still completes
   without crashing the rest of the init.

GREEN -- minimal edit to `scripts\sandbox_init.ps1`:

Replace the block at lines 71-76 with something like:
```powershell
if (Get-Command node -ErrorAction SilentlyContinue) {
    $SharpModule = Join-Path $ProjectRoot "node_modules\sharp"
    if (Test-Path $SharpModule) {
        Write-Host "  -> Node.js + node_modules\sharp found. Starting Sharp Daemon..."
        Start-Process powershell.exe -ArgumentList "-NoExit", "-Command",
            "cd $ProjectRoot; npm start" -WindowStyle Minimized
    } else {
        Write-Host "  -> node_modules\sharp not vendored. Sharp converter will be unavailable. (Run 'npm ci' on the host before launching the sandbox to fix.)" -ForegroundColor Yellow
    }
} else {
    Write-Host "  -> Node.js not found. Sharp converter will be unavailable." -ForegroundColor Yellow
}
```

(`npm start` runs the script defined in `package.json:scripts.start`, which
should be `node app/scripts/sharp_daemon.js`. Verify that field is set;
if not, add it as a co-change.)

Also: confirm `package.json` includes a deterministic
`package-lock.json` so the vendored `node_modules` matches an installable
lockfile -- this is what makes `npm ci --offline` reproducible if you ever
need a hardened re-install path.

## Acceptance criteria

- [ ] `sandbox_init.ps1` contains no bare `npm install` command.
- [ ] When `node_modules\sharp\package.json` is present, the script starts
      Sharp via `npm start` (or equivalent) with no network request.
- [ ] When `node_modules\sharp` is absent, the script logs an actionable
      yellow warning and continues the rest of init (does not abort the
      whole launcher).
- [ ] The text-based test for the script passes.
- [ ] Full `pytest` suite green.
- [ ] No `int()` cast of `quality` in converter code.
- [ ] ASCII-only test code/messages.

## Constraints for the implementer (Sonnet)

TDD only (red before green; here RED is a *static text* assertion on
`sandbox_init.ps1`, which is also valid). No destructive ops, no
`git push` / force / amend / `--no-verify`. Fix exactly this defect -- no
drive-by refactors. The sandbox is the deploy target; behaviour on the
dev host is incidental.
