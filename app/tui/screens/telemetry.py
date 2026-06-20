from app.tui.render import progress_table

def render(state, api, supervisor) -> str:
    if state.active_run_id is None or api is None:
        return "No active run. Submit a batch first."
    try:
        return progress_table(api.get_progress(state.active_run_id))
    except Exception:
        try:
            s = api.get_status(state.active_run_id)
            return f"Run {state.active_run_id}: {s.get('status')}"
        except Exception as e:
            return f"(progress unavailable: {e})"
