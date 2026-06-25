import os
import sys
from ..core.logger import get_logger

log = get_logger(__name__)

def get_bind_host() -> str:
    """Detect the host the server is binding to from env or command line."""
    env_host = os.environ.get("PIXELPIVOT_HOST")
    if env_host:
        return env_host

    try:
        for i, arg in enumerate(sys.argv):
            if arg == "--host" and i + 1 < len(sys.argv):
                return sys.argv[i + 1]
            if arg.startswith("--host="):
                return arg.split("=", 1)[1]
    except Exception:
        pass

    return "127.0.0.1"

def is_exposed_host(host: str) -> bool:
    """Return True if host is not a standard loopback address."""
    normalized = host.strip().lower()
    return normalized not in ("127.0.0.1", "localhost", "::1")

def check_security_config():
    """Verify security requirements are met for network exposure."""
    host = get_bind_host()
    if is_exposed_host(host):
        # 1. Require explicit opt-in
        allow_public = os.environ.get("PIXELPIVOT_ALLOW_PUBLIC") == "1"
        if not allow_public:
            msg = (
                f"Security Risk: Server is binding to '{host}', which exposes it to the network. "
                "To bind to non-loopback interfaces, you must explicitly opt-in by setting "
                "environment variable PIXELPIVOT_ALLOW_PUBLIC=1 or using the --allow-public flag. "
                "For loopback-only use, bind to 127.0.0.1."
            )
            log.error(msg)
            raise RuntimeError(msg)

        # 2. Require shared secret/token
        token = os.environ.get("PIXELPIVOT_API_TOKEN")
        if not token:
            msg = (
                f"Security Risk: Server is binding to '{host}' (exposed to the network) but "
                "no API token is configured. You must set environment variable PIXELPIVOT_API_TOKEN "
                "to a secure shared secret to authorize mutating requests."
            )
            log.error(msg)
            raise RuntimeError(msg)
