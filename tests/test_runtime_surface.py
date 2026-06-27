"""Guard tests for bd-34p: runtime surface is batch + telemetry + hotfolder + calibration.

The deployable backend exposes a calibration trigger (POST /api/v1/calibrate), but
must still not eagerly import the heavyweight calibration engine (cv2/pyvips/the
calibration_runner) at API startup — those imports stay lazy inside the queue
worker. Importing the API entrypoint is done in a subprocess so GUI/calibration
imports triggered elsewhere in the test session can't mask a real coupling regression.
"""
import subprocess
import sys


_PROBE = r"""
import sys
import app.batch_api.main as m

# 1. No GUI / heavyweight calibration engine pulled in by importing the API.
#    The calibration_runner (and its cv2/pyvips deps) must stay lazy — imported
#    only inside the queue worker when a calibration job actually executes.
banned = [name for name in sys.modules
          if name == "streamlit" or name.startswith("streamlit.")
          or "calibration_runner" in name
          or name in ("cv2", "app.core.similarity", "app.core.calibrator")]
assert not banned, f"banned runtime imports: {banned}"

# 2. Routes are limited to batch / telemetry / hotfolder.
paths = set()
for r in m.app.routes:
    if hasattr(r, "path"):
        if r.path.startswith("/api/"):
            paths.add(r.path)
    elif type(r).__name__ == "_IncludedRouter":
        prefix = getattr(r.include_context, "prefix", "")
        for sr in r.original_router.routes:
            p = f"{prefix}{sr.path}"
            if p.startswith("/api/"):
                paths.add(p)

assert paths, "no /api routes registered"
for p in sorted(paths):
    assert (p.startswith("/api/v1/batch")
            or p.startswith("/api/v1/hotfolder")
            or p.startswith("/api/v1/calibrate")), p

print("OK", paths)
"""


def test_api_import_has_no_calibration_or_streamlit_coupling():
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "OK" in result.stdout
