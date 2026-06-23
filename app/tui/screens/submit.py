# app/tui/screens/submit.py
"""Interactive submit screen.

Allows users to input source and target directories with autocomplete, select
tools and target formats via CheckboxList widgets, and start the batch.
"""
from __future__ import annotations

from prompt_toolkit.layout import HSplit, VSplit, Window
from prompt_toolkit.widgets import Frame
from app.tui.screens.base import Screen
from app.tui.widgets import path_field, checkbox_list_wrapper, button
from app.tui.actions import submit
from app.tui.state import UiState, FORMATS


def create_screen(state: UiState, api, supervisor) -> Screen:
    # 1. Path input fields with autocomplete
    src = path_field(
        "Source Dir: ",
        lambda val: setattr(state, "source_dir", val),
        state.source_dir
    )
    dst = path_field(
        "Target Dir: ",
        lambda val: setattr(state, "target_dir", val),
        state.target_dir
    )

    # 2. Selection checkbox lists wrapped in Frames
    tool_choices = [(t, t) for t in state.enabled_tools]
    tools_cb = checkbox_list_wrapper(
        values=tool_choices,
        on_change=lambda vals: setattr(state, "selected_tools", list(vals)),
        initial_values=state.selected_tools
    )

    format_choices = [(f, f) for f in FORMATS]
    formats_cb = checkbox_list_wrapper(
        values=format_choices,
        on_change=lambda vals: setattr(state, "selected_formats", list(vals)),
        initial_values=state.selected_formats
    )

    # 3. Start batch button
    start_btn = button(
        "Start Batch",
        lambda: submit(state, api)
    )

    # 4. Assemble the interactive layout
    container = HSplit([
        src,
        dst,
        Window(height=1),
        VSplit([
            Frame(title="Tools", body=tools_cb),
            Frame(title="Formats", body=formats_cb),
        ], padding=2),
        Window(height=1),
        start_btn,
    ])

    return Screen(container)
