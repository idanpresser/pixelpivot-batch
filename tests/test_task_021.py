"""Task 021 - get_logger must not attach a RotatingFileHandler per-module.

Today every distinct logger name gets its own RotatingFileHandler pointing
at the same file. On Windows the rename during rotation fails because the
other handlers hold the file open. Fix: configure the file handler ONCE
(on a parent / root logger); per-module get_logger returns a plain child
that propagates up.
"""
from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler


def _all_rotating_file_handlers() -> list[tuple[str, RotatingFileHandler]]:
    """Walk every logger in the manager + the root and collect every
    RotatingFileHandler instance currently attached."""
    out: list[tuple[str, RotatingFileHandler]] = []
    for name, obj in logging.Logger.manager.loggerDict.items():
        if isinstance(obj, logging.Logger):
            for h in obj.handlers:
                if isinstance(h, RotatingFileHandler):
                    out.append((name, h))
    for h in logging.root.handlers:
        if isinstance(h, RotatingFileHandler):
            out.append(("<root>", h))
    return out


def test_module_loggers_do_not_own_a_file_handler() -> None:
    """A get_logger("foo") call must NOT attach its own RotatingFileHandler."""
    from app.core.logger import get_logger
    # Use deliberately unique names so the test is robust to other tests'
    # collected loggers.
    names = [
        "task021.alpha.module.one",
        "task021.beta.module.two",
        "task021.gamma.module.three",
    ]
    for n in names:
        logging.Logger.manager.loggerDict.pop(n, None)
    for n in names:
        logger = get_logger(n)
        own_rfh = [h for h in logger.handlers if isinstance(h, RotatingFileHandler)]
        assert not own_rfh, (
            f"{n} owns {len(own_rfh)} RotatingFileHandler(s); fix must move "
            f"the handler to a parent logger and rely on propagation."
        )


def test_single_rotating_file_handler_in_hierarchy() -> None:
    """At most one RotatingFileHandler may target the pixelpivot.log file."""
    from app.core.logger import get_logger
    get_logger("task021.bait.one")
    get_logger("task021.bait.two")
    rfhs = _all_rotating_file_handlers()
    # Allow >= 1 (we expect exactly one), but the FILE they target must be
    # unique. Per-file count = 1.
    by_file: dict[str, list[tuple[str, RotatingFileHandler]]] = {}
    for name, h in rfhs:
        by_file.setdefault(h.baseFilename, []).append((name, h))
    multi = {f: lst for f, lst in by_file.items() if len(lst) > 1}
    assert not multi, (
        f"multiple RotatingFileHandlers target the same file -- rotation will "
        f"race on Windows: {multi}"
    )


def test_emitted_record_reaches_the_file_via_propagation(tmp_path, monkeypatch) -> None:
    """A WARNING from a child logger must reach the file handler via
    propagation, even though the child does not own the handler."""
    # Force the project root to a tmp dir so we don't smash the real log file.
    monkeypatch.setattr("app.core.paths.PROJ_ROOT", tmp_path)
    # Reset the module-level configuration flag so the file handler is
    # rebuilt against the tmp PROJ_ROOT.
    import importlib
    import app.core.logger as L
    importlib.reload(L)
    log = L.get_logger("task021.propagation.child")
    log.warning("task-021-canary-must-land-in-file")
    # Walk the hierarchy for a RFH and flush it.
    for _name, h in _all_rotating_file_handlers():
        try:
            h.flush()
        except Exception:
            pass
    expected = tmp_path / "pixelpivot.log"
    assert expected.exists(), f"log file not created at {expected}"
    text = expected.read_text(encoding="utf-8", errors="replace")
    assert "task-021-canary-must-land-in-file" in text, (
        "WARNING from child logger did not propagate to the file handler"
    )
