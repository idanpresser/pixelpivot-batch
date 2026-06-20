from app.tui.render import tools_table
from app.core import toolcheck

def render(state, api, supervisor) -> str:
    statuses = toolcheck.check_all(ffmpeg_path="ffmpeg", magick_path="magick")
    hint = "\n[s] start sharp  [x] stop sharp  [r] restart sharp"
    return tools_table(statuses) + hint
