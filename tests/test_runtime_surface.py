"""Guard tests for bd-34p: runtime surface is batch-conversion + telemetry + hotfolder.

The deployable backend must not couple to calibration or streamlit at runtime.
Importing the API entrypoint is done in a subprocess so GUI/calibration imports
triggered elsewhere in the test session can't mask a real coupling regression.
"""
import subprocess
import sys


_PROBE = r"""
import sys
import app.batch_api.main as m

# 1. No GUI / calibration engine pulled in by importing the API.
banned = [name for name in sys.modules
          if name == "streamlit" or name.startswith("streamlit.")
          or "calibration" in name.lower()]
assert not banned, f"banned runtime imports: {banned}"

# 2. Routes are limited to batch / telemetry / hotfolder.
paths = sorted({r.path for r in m.app.routes if r.path.startswith("/api/")})
assert paths, "no /api routes registered"
for p in paths:
    assert "calibrat" not in p.lower(), f"calibration endpoint exposed: {p}"
    assert p.startswith("/api/v1/batch") or p.startswith("/api/v1/hotfolder"), p

print("OK", paths)
"""


def test_api_import_has_no_calibration_or_streamlit_coupling():
    result = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "OK" in result.stdout
