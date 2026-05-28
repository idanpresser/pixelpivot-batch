# Task 026 — Single source of truth for the air-gap dependency list

**Severity:** LOW (process risk, not a runtime defect; teammates can let the
two lists drift and only catch it during a sandbox launch.)
**Feature:** Air-gap dependency closure (checklist 1)

## Reproduction

`scripts\download_wheels.ps1:15-33` hardcodes a list of packages to download.
`scripts\sandbox_init.ps1:56` hardcodes the SAME list (almost) to install.
Today they happen to match; nothing enforces it.

## Root cause

No shared source. A future PR that adds e.g. `pillow-heif-decoder` to one
list but not the other ships a broken sandbox closure.

## Required behavior

One file holds the canonical list; both scripts consume it.

## TDD plan

RED -- `tests/test_task_026.py`:

1. Read both scripts as text.
2. Parse out the dependency arrays (`$deps = @(... )` blocks or
   space-separated CLI args).
3. Assert the two lists are identical.

GREEN: move the list into a shared file, e.g. `scripts\air_gap_deps.txt`
(newline-delimited), and have both PS1 scripts read it:
```powershell
$deps = Get-Content "$PSScriptRoot\air_gap_deps.txt" | Where-Object { $_ -and -not $_.StartsWith('#') }
```

## Acceptance criteria

- [ ] One file, two consumers; the lists cannot drift.
- [ ] The test passes.
- [ ] ASCII-only test code/messages.
