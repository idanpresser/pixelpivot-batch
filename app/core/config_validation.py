"""Startup config validation + fail-fast (E8 8.2).

The engine reads ~10 numeric env tunables (RAM headroom, worker byte budgets,
timeout scaling, disk backpressure, ...) that are otherwise consumed with no
validation — a bad value surfaces cryptically deep inside a batch (a bare
``float('abc')`` at some import site), long after the operator could have fixed
it. This module is the single boot-time gate: it parses every tunable, checks
type and range, and raises a clear, var-named :class:`ConfigValidationError`
before any work starts. On a valid boot it returns the resolved effective
config so the service can log it once.

Kept dependency-free (no import of app.core.config or the converter base) so it
can run at the very top of startup, before the modules that consume these
values are imported.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Mapping, Optional

from .logger import get_logger

log = get_logger(__name__)


class ConfigValidationError(ValueError):
    """Raised at startup when an env tunable has an invalid type or range."""


def _pct(v: float) -> bool:
    return 0.0 < v <= 100.0


def _unit(v: float) -> bool:
    return 0.0 < v <= 1.0


class _Spec:
    """One validated tunable: parser, range predicate, default, expectation."""

    __slots__ = ("env", "parse", "ok", "default", "expects", "optional")

    def __init__(
        self,
        env: str,
        parse: Callable[[str], Any],
        ok: Callable[[Any], bool],
        default: Any,
        expects: str,
        optional: bool = False,
    ) -> None:
        self.env = env
        self.parse = parse
        self.ok = ok
        self.default = default
        self.expects = expects
        self.optional = optional

    def resolve(self, env: Mapping[str, str]) -> Any:
        raw = env.get(self.env)
        if raw is None or (self.optional and raw == ""):
            return self.default
        try:
            value = self.parse(raw)
        except (ValueError, TypeError):
            raise ConfigValidationError(
                f"{self.env}={raw!r} is invalid: expected {self.expects}."
            )
        if not self.ok(value):
            raise ConfigValidationError(
                f"{self.env}={raw!r} is out of range: expected {self.expects}."
            )
        return value


# The tunables validated at boot. Booleans (METRICS/OTEL/CALIBRATION/IMAGE2_*)
# are intentionally omitted: they already parse tolerantly (any non-matching
# string reads as false) and cannot fail.
_SPECS: tuple[_Spec, ...] = (
    _Spec("PIXELPIVOT_SHUTDOWN_GRACE_S", float, lambda v: v > 0, 30.0, "a float > 0"),
    _Spec("PIXELPIVOT_BATCH_FATAL_ABORT_THRESHOLD", int, lambda v: v >= 1, 3, "an int >= 1"),
    _Spec("PIXELPIVOT_CONCURRENT_ENCODES_SCALING_FACTOR", float, lambda v: v > 0, 2.0, "a float > 0"),
    _Spec("PIXELPIVOT_CONCURRENT_ENCODES_MAX_WORKERS", int, lambda v: v >= 1, None, "an int >= 1", optional=True),
    _Spec("PIXELPIVOT_WORKER_BYTES_PER_PX", int, lambda v: v > 0, 12, "an int > 0"),
    _Spec("PIXELPIVOT_WORKER_RAM_HEADROOM", float, _unit, 0.7, "a float in (0, 1]"),
    _Spec("PIXELPIVOT_WORKER_UNKNOWN_MP", float, lambda v: v > 0, 24.0, "a float > 0"),
    _Spec("PIXELPIVOT_CHUNK_RAM_FRACTION", float, _unit, 0.25, "a float in (0, 1]"),
    _Spec("PIXELPIVOT_DISK_BACKPRESSURE_PCT", float, _pct, 90.0, "a float in (0, 100]"),
    _Spec("PIXELPIVOT_QUEUE_POLL_S", float, lambda v: v > 0, 0.5, "a float > 0"),
)


def validate_startup_config(env: Optional[Mapping[str, str]] = None) -> Dict[str, Any]:
    """Validate every env tunable and return the resolved effective config.

    Args:
        env: Mapping to read from (defaults to ``os.environ``).

    Returns:
        Dict of env-var name -> resolved (parsed, range-checked) value, with
        defaults filled in for anything unset.

    Raises:
        ConfigValidationError: If any tunable has a bad type or out-of-range
            value. The message names the offending variable and its value.
    """
    if env is None:
        import os

        env = os.environ
    return {spec.env: spec.resolve(env) for spec in _SPECS}


def validate_and_log_startup_config(
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Validate config and log the resolved effective config exactly once."""
    resolved = validate_startup_config(env)
    rendered = ", ".join(f"{k}={v}" for k, v in resolved.items())
    log.info("Resolved effective config: %s", rendered)
    return resolved
