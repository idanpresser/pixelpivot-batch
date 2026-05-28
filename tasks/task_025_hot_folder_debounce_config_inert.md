# Task 025 — Wire HOT_FOLDER_DEBOUNCE_MS into the handler

**Severity:** LOW (knob is inert; default value happens to match the desired
behavior, so there's no observable regression today.)
**Feature:** D2 (hot folder debounce)

## Reproduction

`app/core/config.py:185`:
```
HOT_FOLDER_DEBOUNCE_MS = 5000
```

`app/batch_api/hot_folder.py:206`:
```python
handler = HotFolderHandler(self.orchestrator, self.loop, cfg_dict)
```
The constructor signature on line 28-30 is:
```python
def __init__(self, orchestrator, loop: asyncio.AbstractEventLoop,
             config: Dict[str, Any], debounce_seconds: float = 5.0):
```
`debounce_seconds` is never passed by the manager, so the config knob is
silently ignored. Operators editing `HOT_FOLDER_DEBOUNCE_MS` see no effect.

## Root cause

The `HotFolderManager.add_hot_folder` call site does not thread the config
value through. The two numbers (5000 ms / 5.0 s) coincidentally agree,
which is why the bug has been latent.

## Required behavior

Edits to `HOT_FOLDER_DEBOUNCE_MS` change the actual debounce window.

## TDD plan

RED -- `tests/test_task_025.py`:

1. Monkeypatch `app.core.config.HOT_FOLDER_DEBOUNCE_MS = 1500`.
2. Construct a `HotFolderManager` with a stub orchestrator + asyncio loop.
3. Register a hot folder; pull the registered handler; assert
   `handler.debounce_seconds == 1.5`. Fails today (always 5.0).

GREEN -- one line in `hot_folder.py:206`:
```python
handler = HotFolderHandler(self.orchestrator, self.loop, cfg_dict,
                           debounce_seconds=HOT_FOLDER_DEBOUNCE_MS / 1000.0)
```
Plus the import of `HOT_FOLDER_DEBOUNCE_MS` at the top.

## Acceptance criteria

- [ ] Changing the config constant changes the actual handler debounce.
- [ ] Full `pytest` suite green.
- [ ] ASCII-only test code/messages.
