# app/tui/screens/base.py
from __future__ import annotations

from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import AnyContainer

class Screen:
    def __init__(self, container: AnyContainer, key_bindings: KeyBindings | None = None):
        self.container = container
        self.key_bindings = key_bindings
