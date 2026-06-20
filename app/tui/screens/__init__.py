from . import submit, telemetry, history, tools, settings
RENDERERS = {
    "submit": submit.render, "telemetry": telemetry.render,
    "history": history.render, "tools": tools.render, "settings": settings.render,
}
