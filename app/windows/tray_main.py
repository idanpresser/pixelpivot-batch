"""Entry point for PixelPivotTray.exe."""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    from PySide6.QtWidgets import QApplication
    from app.windows.tray import PixelPivotTray

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if getattr(sys, "frozen", False):
        # Both exes ship in the same dist/ directory
        exe_dir = Path(sys.executable).parent
    else:
        # Dev: check both merged and unmerged paths
        root_dir = Path(__file__).parent.parent.parent
        exe_dir = root_dir / "dist" / "PixelPivot"
        if not (exe_dir / "PixelPivotService.exe").exists():
            exe_dir = root_dir / "dist" / "pixelpivot_service"

    from app.windows._settings import resolve_data_dir
    service_exe = exe_dir / "PixelPivotService.exe"
    log_dir = resolve_data_dir() / "logs"

    _tray = PixelPivotTray(app, service_exe, log_dir)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
