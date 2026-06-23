# app/tui/widgets.py
"""Shared builders for interactive prompt_toolkit widgets.

Includes a labeled path field with PathCompleter, checkbox list wrapper,
button, and toast display area.
"""
from __future__ import annotations

from typing import Any, Callable, List, Tuple
from prompt_toolkit.completion import PathCompleter
from prompt_toolkit.layout import VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Button, CheckboxList, Label, TextArea
from prompt_toolkit.formatted_text import ANSI


class ObservableCheckboxList(CheckboxList):
    def __init__(self, values: List[Tuple[Any, str]], on_change: Callable[[List[Any]], None] | None = None, **kwargs):
        self.on_change = on_change
        super().__init__(values, **kwargs)

    def _handle_enter(self) -> None:
        super()._handle_enter()
        if self.on_change:
            self.on_change(self.current_values)


def path_field(label_text: str, text_changed_handler: Callable[[str], None] | None = None, initial_text: str = "") -> VSplit:
    ta = TextArea(
        text=initial_text,
        completer=PathCompleter(only_directories=True),
        multiline=False,
    )
    if text_changed_handler:
        ta.buffer.on_text_changed += lambda buffer: text_changed_handler(buffer.text)
    
    # Store reference to inner text area so caller can access/focus it if needed
    container = VSplit([
        Label(text=label_text, width=12),
        ta
    ], padding=1)
    container.text_area = ta
    return container


def checkbox_list_wrapper(values: List[Tuple[Any, str]], on_change: Callable[[List[Any]], None] | None = None, initial_values: List[Any] | None = None) -> ObservableCheckboxList:
    cb = ObservableCheckboxList(values=values, on_change=on_change)
    if initial_values is not None:
        cb.current_values = list(initial_values)
    return cb


def button(text: str, handler: Callable[[], None]) -> Button:
    return Button(text=text, handler=handler)


def toast_area(state: Any) -> Window:
    def get_toast_text():
        if state.toast:
            return ANSI(f"\u001b[91mToast: {state.toast}\u001b[0m")
        return ANSI("")
    return Window(FormattedTextControl(get_toast_text), height=1)
