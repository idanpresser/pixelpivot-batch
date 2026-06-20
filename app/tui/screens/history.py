from app.tui.render import history_table

def render(state, api, supervisor) -> str:
    if api is None:
        return "(no api)"
    try:
        return history_table(api.get_history())
    except Exception as e:
        return f"(history unavailable: {e})"
