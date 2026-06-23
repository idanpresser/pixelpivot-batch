# TUI Interactive UX — Design

**Date:** 2026-06-23
**Status:** Approved design, pre-implementation
**Builds on:** `docs/superpowers/specs/2026-06-20-tui-control-plane-design.md` (control plane),
`docs/TUI_GUIDE.md` (current behavior). The control-plane backend + TUI shell are
merged to `main`; this spec makes the TUI **interactive**.

## 1. Purpose

The current TUI (`app/tui/`) renders read-only screens via static
`FormattedTextControl` functions; only `Tab` (switch tab) and `q` (quit) are
bound. Selection logic exists in `state.py` (`toggle_tool`, `toggle_format`,
`build_batch_payload`) but nothing is wired to keys, and there is no batch
submission, control, or progress bar from inside the TUI.

This spec adds full interactivity: select source/target paths, tools, and
formats; start a batch; control it (pause/resume/stop/restart); start/stop the
sharp daemon; edit settings; and watch a live progress bar.

## 2. Decisions (locked)

| Topic | Decision |
|---|---|
| Tool/format selection | Focusable `CheckboxList` widgets (arrows move, Space toggles). |
| Path entry | Editable `TextArea` + `PathCompleter` (Tab completes directory entries). |
| Progress bar | Rich progress bar rendered to ANSI (matches existing `render.py` pattern); cell-granular (`cells_done/cells_total`). |
| Scope | Full interactivity: submit, control, sharp lifecycle, settings editing. |
| Key scheme | `1`–`5` jump to tabs; `Tab`/`Shift-Tab` move field focus; `Enter` activates button; `Space` toggles checkbox; `q`/`Ctrl-Q` quit. |
| Rendering | Hybrid: real widgets for input, Rich-to-ANSI for display (progress, tables, health board). |
| Refresh | `Application(refresh_interval=1.0)` + a background poller thread for live progress (no blocking HTTP on the UI thread). |

## 3. Key-scheme edge case (must implement)

Editable text fields capture digits and letters. So global tab/quit keys
**must be guarded by a focus filter**: the `1`–`5` tab keys and the bare `q`
quit fire only when focus is **not** on an editable field. Resolution:
- `Esc` moves focus back to the tab bar (where number keys always work).
- `Ctrl-Q` always quits, regardless of focus, as a safe fallback.
- Implement with a prompt_toolkit `Condition`/`has_focus` filter on the global
  bindings; digits and `q` type literally while a `TextArea` is focused.

## 4. Architecture

Rework `app/tui/app.py` from static render-windows into a focusable interactive
layout. Hybrid: prompt_toolkit widgets (`TextArea`+`PathCompleter`,
`CheckboxList`, `Button`) for input; Rich-rendered ANSI for display. A
background poller thread fetches `/batch/{id}/progress` into `state` and calls
`app.invalidate()` so the UI thread never blocks on HTTP.

```
key -> screen/global handler -> actions.* -> api / supervisor + mutate state
                                                 |
                                                 v
                                           app.invalidate() -> repaint
poller thread (telemetry active): api.get_progress(run_id) -> state.progress_cache -> invalidate
```

## 5. Components / files

- `app/tui/app.py` — **rebuilt** shell: tab bar, body container swapped per
  `active_tab`, log panel, status bar, toast line; global keybindings (1–5 tabs,
  `Esc` defocus, `Ctrl-Q`/guarded `q` quit) with focus filter; `refresh_interval`.
- `app/tui/widgets.py` — **new** shared builders: labeled path field with
  `PathCompleter`, checkbox list wrapper, button, toast area.
- `app/tui/actions.py` — **new** handlers wiring state + api + supervisor:
  - `submit(state, api)` — `build_batch_payload` → `api.start_batch` → set
    `state.active_run_id` → switch to telemetry; ValueError/API error → toast.
  - `control(state, api, action)` — `api.control(run_id, action)`; restart via
    `api.restart(run_id)`.
  - `sharp(state, supervisor, action)` — start/stop/restart the sharp child via
    `supervisor` using `settings.tools.sharp_script` + `settings.paths.sharp_port`.
  - `save_settings(state, path)` — write `settings.toml`; apply live keys to
    running `state` immediately; flag restart-required keys via toast.
- `app/tui/screens/*.py` — each exposes a `Screen` object with `.container`
  (widgets) and optional per-screen keybindings, replacing `render()->str`.
- `app/tui/render.py` — add `progress_bar(p)` Rich renderable (bar + percent +
  cells + ok/fail + cpu/ram).
- `app/tui/state.py` — add `settings: dict`, `toast: str | None`,
  `progress_cache: dict`. Existing selection logic reused.

## 6. Beads (decomposition)

| Bead | Scope | Depends on |
|---|---|---|
| **shell** | Interactive layout, focus + key scheme (incl. §3 filter), `refresh_interval`, poller thread, toast area, `widgets.py` + `actions.py` scaffolding. Foundation. | — |
| **submit** | Path-autocomplete fields + tool/format `CheckboxList` + `[Start]` button + `submit` action. | shell |
| **telemetry** | Rich progress bar + control keys `p`/`r`/`s`/`R` → `control`/`restart`. | shell |
| **tools** | Sharp daemon start/stop/restart actions + live health-board refresh. | shell |
| **settings** | Editable form + `tools.enabled` checkboxlist + `[Save]`; live-apply vs restart-required. | shell |

Build order: **shell → {submit, telemetry, tools, settings}** (the four are
parallel once shell lands).

## 7. Error handling

All `actions.*` catch validation (`build_batch_payload` `ValueError`) and
api/supervisor exceptions and surface them on the **toast** line; the screen
stays put and nothing crashes. Progress `404` (run finalized) → telemetry shows
the final `/batch/status` summary instead of the bar. Poller-thread exceptions
are swallowed (transient API-down) and retried on the next tick.

## 8. Testing

- **actions** (pure, fake api/supervisor):
  - submit calls `start_batch`, sets `active_run_id`, switches to telemetry;
    bad selection → `state.toast` set, no api call.
  - control calls `api.control` with the right action; restart calls `api.restart`.
  - sharp calls `supervisor.start("sharp", [...])` / `stop` / `restart`.
  - `save_settings` writes the file and applies live keys to `state`; restart
    keys produce a flagging toast.
- **widgets/state**: toggling a checkbox reflects into `state.selected_tools` /
  `state.selected_formats`.
- **app smoke** (pt `create_pipe_input` + `DummyOutput`, or direct handler calls
  with a fake event): `2` switches to telemetry; `Tab` moves focus; `Space`
  toggles a checkbox; bare `q` is a no-op while a `TextArea` is focused; `Ctrl-Q`
  exits.
- No emoji/icons in tests (Python console encoding rule).

## 9. Out of scope (YAGNI)

Mouse support; sub-cell progress (data is cell-granular only); directory-tree
browser (autocomplete chosen instead); recent-paths history dropdown.

## 10. Accepted constraints

- Progress bar resolution = matrix cells, not per-image.
- Switching tabs while editing a field requires `Esc` first (focus filter).
- Settings restart-required keys (`api.*`, `paths.*`, `tools.ffmpeg/magick`,
  `limits.max_workers`) take effect only after the API child restarts.
