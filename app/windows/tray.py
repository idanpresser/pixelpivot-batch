"""PySide6 system tray control panel for PixelPivot Batch Engine.

Exposes the full batch API surface through tray submenus and dialogs:
- Batch jobs: start, pause/resume/stop, restart (last 6 shown)
- Hot folders: register, unregister
- Calibration: trigger with full parameter control
- Settings: all env-var-backed config options persisted to data/pixelpivot_config.json
- Service: start/stop/install/uninstall with automatic UAC elevation
- Logs: live-tailing log viewer

API polling runs in a daemon thread to avoid blocking the Qt event loop.
Settings are written to data/pixelpivot_config.json; service_main.py applies
them as env vars before launching child processes.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any

if sys.platform != "win32":
    raise ImportError("app.windows.tray requires Windows")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QMenu,
    QWidget,
)

from app.windows import elevation, scm
from app.windows._settings import SETTINGS_DEFAULTS as _DEFAULTS, SETTINGS_ENV_MAP


# ---------------------------------------------------------------------------
# Icon
# ---------------------------------------------------------------------------

def _make_icon(color: str = "#2563eb") -> QIcon:
    px = QPixmap(64, 64)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(QColor("#1e40af"))
    p.drawEllipse(2, 2, 60, 60)
    font = QFont("Arial", 20, QFont.Weight.Bold)
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "PP")
    p.end()
    return QIcon(px)


# ---------------------------------------------------------------------------
# API client  (stdlib only — no requests dep in tray exe)
# ---------------------------------------------------------------------------

class _Api:
    BASE_API = "http://localhost:8000/api/v1"
    BASE     = "http://localhost:8000"
    TIMEOUT  = 1.5  # seconds; kept short so background thread doesn't lag

    def _req(self, method: str, url: str, body: Any = None) -> Any:
        data    = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data else {}
        req     = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as resp:
                return json.loads(resp.read())
        except Exception:
            return None

    def health(self) -> dict | None:
        return self._req("GET", f"{self.BASE}/healthz/ready")

    def batch_history(self) -> list:
        r = self._req("GET", f"{self.BASE_API}/batch/history")
        return r if isinstance(r, list) else []

    def batch_control(self, run_id: int, action: str) -> dict | None:
        return self._req("POST", f"{self.BASE_API}/batch/{run_id}/control", {"action": action})

    def batch_restart(self, run_id: int) -> dict | None:
        return self._req("POST", f"{self.BASE_API}/batch/{run_id}/restart", {})

    def batch_start(self, payload: dict) -> dict | None:
        return self._req("POST", f"{self.BASE_API}/batch/start", payload)

    def hotfolders(self) -> list:
        r = self._req("GET", f"{self.BASE_API}/hotfolder/list")
        return r if isinstance(r, list) else []

    def hotfolder_register(self, payload: dict) -> dict | None:
        return self._req("POST", f"{self.BASE_API}/hotfolder/register", payload)

    def hotfolder_delete(self, watcher_id: Any) -> dict | None:
        return self._req("DELETE", f"{self.BASE_API}/hotfolder/{watcher_id}")

    def calibrate(self, payload: dict) -> dict | None:
        return self._req("POST", f"{self.BASE_API}/calibrate", payload)


_api = _Api()


# ---------------------------------------------------------------------------
# Settings  (JSON file → env vars applied by service_main at child launch)
# ---------------------------------------------------------------------------

# _DEFAULTS and SETTINGS_ENV_MAP imported from app.windows._settings above.


class _Settings:
    def __init__(self, data_dir: Path) -> None:
        self._path = data_dir / "pixelpivot_config.json"

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return dict(_DEFAULTS)
        try:
            return {**_DEFAULTS, **json.loads(self._path.read_text())}
        except Exception:
            return dict(_DEFAULTS)

    def save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# Shared form helpers
# ---------------------------------------------------------------------------

_TOOLS   = ["magick", "ffmpeg", "vips", "sharp", "cavif"]
_FORMATS = ["webp", "avif", "jxl"]


def _dir_row(placeholder: str = "") -> tuple[QWidget, QLineEdit]:
    row  = QWidget()
    h    = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    edit = QLineEdit()
    edit.setPlaceholderText(placeholder)
    btn  = QPushButton("Browse...")
    btn.setFixedWidth(70)
    btn.clicked.connect(lambda: _pick_dir(edit))
    h.addWidget(edit)
    h.addWidget(btn)
    return row, edit


def _pick_dir(edit: QLineEdit) -> None:
    d = QFileDialog.getExistingDirectory(None, "Select folder", edit.text() or "")
    if d:
        edit.setText(d)


def _tool_fmt_widgets() -> tuple[QListWidget, QListWidget]:
    tl = QListWidget()
    tl.addItems(_TOOLS)
    tl.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
    tl.setFixedHeight(100)
    fl = QListWidget()
    fl.addItems(_FORMATS)
    fl.setSelectionMode(QListWidget.SelectionMode.MultiSelection)
    fl.setFixedHeight(70)
    return tl, fl


def _selected(lw: QListWidget) -> list[str]:
    return [i.text() for i in lw.selectedItems()]


# ---------------------------------------------------------------------------
# Settings dialog
# ---------------------------------------------------------------------------

class SettingsDialog(QDialog):
    def __init__(self, settings: _Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PixelPivot — Settings")
        self.resize(440, 380)
        self._settings = settings
        self._data     = settings.load()

        tabs = QTabWidget()
        tabs.addTab(self._perf_tab(),  "Performance")
        tabs.addTab(self._cal_tab(),   "Calibration")
        tabs.addTab(self._adv_tab(),   "Advanced")

        note = QLabel("Changes take effect after service restart.")
        note.setStyleSheet("color: gray; font-size: 10px;")

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(note)
        lay.addWidget(btns)

    # --- tab builders ---

    def _perf_tab(self) -> QWidget:
        w    = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)

        self._scaling = QDoubleSpinBox()
        self._scaling.setRange(0.5, 16.0)
        self._scaling.setSingleStep(0.5)
        self._scaling.setValue(float(self._data["concurrent_encodes_scaling_factor"]))
        form.addRow("Thread scaling factor (× CPU count):", self._scaling)

        self._max_w = QSpinBox()
        self._max_w.setRange(0, 256)
        self._max_w.setSpecialValueText("Auto")
        v = self._data["concurrent_encodes_max_workers"]
        self._max_w.setValue(0 if v is None else int(v))
        form.addRow("Max worker threads (0 = auto):", self._max_w)

        self._ram = QDoubleSpinBox()
        self._ram.setRange(0.05, 0.90)
        self._ram.setSingleStep(0.05)
        self._ram.setDecimals(2)
        self._ram.setSuffix("  (fraction of RAM)")
        self._ram.setValue(float(self._data["chunk_ram_fraction"]))
        form.addRow("FFmpeg chunk RAM budget:", self._ram)

        self._disk = QDoubleSpinBox()
        self._disk.setRange(50.0, 99.0)
        self._disk.setSingleStep(1.0)
        self._disk.setSuffix(" %")
        self._disk.setValue(float(self._data["disk_backpressure_pct"]))
        form.addRow("Disk backpressure threshold:", self._disk)

        self._fatal = QSpinBox()
        self._fatal.setRange(1, 20)
        self._fatal.setValue(int(self._data["batch_fatal_abort_threshold"]))
        form.addRow("Fatal error abort threshold:", self._fatal)

        return w

    def _cal_tab(self) -> QWidget:
        w    = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)

        self._cal_en = QCheckBox("Enable SSIM-target quality calibration")
        self._cal_en.setChecked(bool(self._data["calibration_enabled"]))
        form.addRow("", self._cal_en)

        self._img2 = QCheckBox("Allow image2 demuxer for avif / jxl (experimental)")
        self._img2.setChecked(bool(self._data["image2_allow_lossy"]))
        form.addRow("", self._img2)

        hint = QLabel(
            "Calibration populates the heuristic quality table.\n"
            "Trigger runs via Batch Jobs > Calibrate Now..."
        )
        hint.setStyleSheet("color: gray; font-size: 10px;")
        form.addRow("", hint)

        return w

    def _adv_tab(self) -> QWidget:
        w    = QWidget()
        form = QFormLayout(w)
        form.setContentsMargins(12, 12, 12, 12)

        self._grace = QDoubleSpinBox()
        self._grace.setRange(5.0, 300.0)
        self._grace.setSingleStep(5.0)
        self._grace.setSuffix(" s")
        self._grace.setValue(float(self._data["shutdown_grace_s"]))
        form.addRow("Shutdown grace period:", self._grace)

        self._poll = QDoubleSpinBox()
        self._poll.setRange(0.1, 10.0)
        self._poll.setSingleStep(0.1)
        self._poll.setDecimals(1)
        self._poll.setSuffix(" s")
        self._poll.setValue(float(self._data["queue_poll_s"]))
        form.addRow("Queue poll interval:", self._poll)

        self._metrics = QCheckBox("Enable Prometheus /metrics endpoint")
        self._metrics.setChecked(bool(self._data["metrics_enabled"]))
        form.addRow("", self._metrics)

        return w

    def _save(self) -> None:
        mw = self._max_w.value()
        self._data.update({
            "concurrent_encodes_scaling_factor": self._scaling.value(),
            "concurrent_encodes_max_workers":    None if mw == 0 else mw,
            "chunk_ram_fraction":                self._ram.value(),
            "disk_backpressure_pct":             self._disk.value(),
            "batch_fatal_abort_threshold":       self._fatal.value(),
            "calibration_enabled":               self._cal_en.isChecked(),
            "image2_allow_lossy":                self._img2.isChecked(),
            "shutdown_grace_s":                  self._grace.value(),
            "queue_poll_s":                      self._poll.value(),
            "metrics_enabled":                   self._metrics.isChecked(),
        })
        self._settings.save(self._data)
        self.accept()


# ---------------------------------------------------------------------------
# Start Batch dialog
# ---------------------------------------------------------------------------

class StartBatchDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Start New Batch")
        self.resize(480, 420)
        self.payload: dict | None = None

        form = QFormLayout()

        src_row, self._src = _dir_row("Source directory")
        form.addRow("Source folder:", src_row)

        tgt_row, self._tgt = _dir_row("Output directory")
        form.addRow("Target folder:", tgt_row)

        self._tools, self._fmts = _tool_fmt_widgets()
        form.addRow("Tools:", self._tools)
        form.addRow("Formats:", self._fmts)

        self._cat = QLineEdit("general")
        form.addRow("Category (comma-sep):", self._cat)

        self._sample = QSpinBox()
        self._sample.setRange(0, 100_000)
        self._sample.setSpecialValueText("All files")
        form.addRow("Sample size (0 = all):", self._sample)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._submit)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btns)

    def _submit(self) -> None:
        tools = _selected(self._tools)
        fmts  = _selected(self._fmts)
        src   = self._src.text().strip()
        tgt   = self._tgt.text().strip()
        if not src:
            QMessageBox.warning(self, "Missing", "Source folder required.")
            return
        if not tgt:
            QMessageBox.warning(self, "Missing", "Target folder required.")
            return
        if not tools:
            QMessageBox.warning(self, "Missing", "Select at least one tool.")
            return
        if not fmts:
            QMessageBox.warning(self, "Missing", "Select at least one format.")
            return
        cats   = [c.strip() for c in self._cat.text().split(",") if c.strip()] or ["general"]
        sample = self._sample.value() or None
        self.payload = {
            "source_dir":    src,
            "target_dir":    tgt,
            "tool":          tools,
            "target_format": fmts,
            "category":      cats,
            "trigger_type":  "manual",
            **({"sample": sample} if sample else {}),
        }
        self.accept()


# ---------------------------------------------------------------------------
# Register Hot Folder dialog
# ---------------------------------------------------------------------------

class RegisterHotFolderDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Register Hot Folder")
        self.resize(480, 380)
        self.payload: dict | None = None

        form = QFormLayout()

        src_row, self._src = _dir_row("Watched directory")
        form.addRow("Watch folder:", src_row)

        tgt_row, self._tgt = _dir_row("Output directory")
        form.addRow("Output folder:", tgt_row)

        self._tools, self._fmts = _tool_fmt_widgets()
        form.addRow("Tools:", self._tools)
        form.addRow("Formats:", self._fmts)

        self._cat = QLineEdit("general")
        form.addRow("Category (comma-sep):", self._cat)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._submit)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btns)

    def _submit(self) -> None:
        tools = _selected(self._tools)
        fmts  = _selected(self._fmts)
        src   = self._src.text().strip()
        tgt   = self._tgt.text().strip()
        if not src or not tgt:
            QMessageBox.warning(self, "Missing", "Both folders required.")
            return
        if not tools or not fmts:
            QMessageBox.warning(self, "Missing", "Select at least one tool and format.")
            return
        cats = [c.strip() for c in self._cat.text().split(",") if c.strip()] or ["general"]
        self.payload = {
            "source_dir":    src,
            "target_dir":    tgt,
            "tool":          tools,
            "target_format": fmts,
            "category":      cats,
        }
        self.accept()


# ---------------------------------------------------------------------------
# Calibration dialog
# ---------------------------------------------------------------------------

class CalibrateDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Run Calibration")
        self.resize(480, 360)
        self.payload: dict | None = None

        form = QFormLayout()

        src_row, self._src = _dir_row("Directory with sample images")
        form.addRow("Sample images:", src_row)

        self._tools, self._fmts = _tool_fmt_widgets()
        form.addRow("Tools:", self._tools)
        form.addRow("Formats:", self._fmts)

        self._cat = QLineEdit("general")
        form.addRow("Category (comma-sep):", self._cat)

        self._sample = QSpinBox()
        self._sample.setRange(2, 10_000)
        self._sample.setValue(30)
        form.addRow("Max sample images:", self._sample)

        self._ssim = QDoubleSpinBox()
        self._ssim.setRange(0.80, 0.999)
        self._ssim.setSingleStep(0.005)
        self._ssim.setDecimals(3)
        self._ssim.setValue(0.98)
        form.addRow("Target SSIM:", self._ssim)

        self._regen = QCheckBox("Regenerate heuristic table after calibration")
        self._regen.setChecked(True)
        form.addRow("", self._regen)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._submit)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(btns)

    def _submit(self) -> None:
        tools = _selected(self._tools)
        fmts  = _selected(self._fmts)
        src   = self._src.text().strip()
        if not src:
            QMessageBox.warning(self, "Missing", "Sample images folder required.")
            return
        if not tools or not fmts:
            QMessageBox.warning(self, "Missing", "Select at least one tool and format.")
            return
        cats = [c.strip() for c in self._cat.text().split(",") if c.strip()] or ["general"]
        self.payload = {
            "source_dir":       src,
            "tool":             tools,
            "target_format":    fmts,
            "category":         cats,
            "sample":           self._sample.value(),
            "target_ssim":      self._ssim.value(),
            "regenerate_table": self._regen.isChecked(),
        }
        self.accept()


# ---------------------------------------------------------------------------
# Log viewer
# ---------------------------------------------------------------------------

class LogWindow(QDialog):
    _READ_CHUNK = 32_768

    def __init__(self, log_dir: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("PixelPivot — Service Logs")
        self.resize(860, 520)
        self._log_dir = log_dir
        self._pos: dict[str, int] = {}

        lay = QVBoxLayout(self)
        self._combo = QComboBox()
        lay.addWidget(self._combo)
        self._text = QPlainTextEdit(readOnly=True)
        self._text.setFont(QFont("Consolas", 9))
        lay.addWidget(self._text)

        self._combo.currentTextChanged.connect(self._switch_log)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        self._populate_combo()

    def _log_files(self) -> list[Path]:
        if not self._log_dir.exists():
            return []
        return sorted(self._log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)

    def _populate_combo(self) -> None:
        cur = self._combo.currentText()
        self._combo.blockSignals(True)
        self._combo.clear()
        for f in self._log_files():
            self._combo.addItem(f.name)
        idx = self._combo.findText(cur)
        self._combo.setCurrentIndex(max(0, idx))
        self._combo.blockSignals(False)
        self._switch_log(self._combo.currentText())

    def _switch_log(self, name: str) -> None:
        self._text.clear()
        self._pos[name] = 0
        self._refresh()

    def _refresh(self) -> None:
        name = self._combo.currentText()
        if not name:
            return
        path = self._log_dir / name
        if not path.exists():
            return
        pos = self._pos.get(name, 0)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                fh.seek(pos)
                chunk = fh.read(self._READ_CHUNK)
                if chunk:
                    self._text.appendPlainText(chunk.rstrip("\n"))
                    self._pos[name] = fh.tell()
                    sb = self._text.verticalScrollBar()
                    sb.setValue(sb.maximum())
        except OSError:
            pass


# ---------------------------------------------------------------------------
# System tray
# ---------------------------------------------------------------------------

class PixelPivotTray(QSystemTrayIcon):
    def __init__(
        self,
        app: QApplication,
        service_exe: Path,
        log_dir: Path,
    ) -> None:
        super().__init__(_make_icon(), app)
        self._app         = app
        self._service_exe = service_exe
        self._log_dir     = log_dir
        self._log_window: LogWindow | None = None
        self._settings    = _Settings(log_dir.parent)   # data/ dir
        self._api_cache: dict[str, Any] = {}             # written by background thread

        menu = QMenu()

        # Non-clickable status label at top
        self._act_status = menu.addAction("Status: checking...")
        self._act_status.setEnabled(False)
        menu.addSeparator()

        self._act_open_ui  = menu.addAction("Open GUI")
        self._act_open_api = menu.addAction("Open API Docs")
        menu.addSeparator()

        self._batch_menu = menu.addMenu("Batch Jobs")
        self._hf_menu    = menu.addMenu("Hot Folders")
        menu.addAction("Calibrate Now...").triggered.connect(self._show_calibrate)
        menu.addSeparator()

        menu.addAction("Settings...").triggered.connect(self._show_settings)
        menu.addSeparator()

        svc_menu = menu.addMenu("Service")
        self._act_start     = svc_menu.addAction("Start")
        self._act_stop      = svc_menu.addAction("Stop")
        svc_menu.addSeparator()
        self._act_install   = svc_menu.addAction("Install")
        self._act_uninstall = svc_menu.addAction("Uninstall")

        menu.addAction("View Logs...").triggered.connect(self._show_logs)
        menu.addSeparator()
        menu.addAction("Exit").triggered.connect(app.quit)

        self.setContextMenu(menu)
        self.setToolTip("PixelPivot Batch Engine")

        self._act_open_ui.triggered.connect(lambda: webbrowser.open("http://localhost:8503"))
        self._act_open_api.triggered.connect(lambda: webbrowser.open("http://localhost:8000/docs"))
        self._act_start.triggered.connect(self._svc_start)
        self._act_stop.triggered.connect(self._svc_stop)
        self._act_install.triggered.connect(self._svc_install)
        self._act_uninstall.triggered.connect(self._svc_uninstall)

        # Rebuild batch/hf menus with empty placeholder until first poll
        self._rebuild_batch_menu([])
        self._rebuild_hf_menu([])

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(5000)
        self._poll_timer.timeout.connect(self._update_state)
        self._poll_timer.start()
        self._update_state()

        self.show()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def _update_state(self) -> None:
        state     = scm.get_state()
        running   = state == "running"
        installed = state != "not_installed"
        busy      = state in ("starting", "stopping")

        self._act_start.setEnabled(installed and not running and not busy)
        self._act_stop.setEnabled(running)
        self._act_install.setEnabled(not installed)
        self._act_uninstall.setEnabled(installed and not running and not busy)

        if running:
            # Kick off background API refresh; UI reads stale cache from last tick.
            threading.Thread(target=self._fetch_api, daemon=True).start()
            cache    = self._api_cache
            jobs     = cache.get("jobs", [])
            hfs      = cache.get("hfs", [])
            health   = cache.get("health")
            active   = [j for j in jobs if j.get("status") in ("running", "paused")]
            if health is not None:
                api_status = "API ready" if health.get("ready") else "API not ready"
            else:
                api_status = "API unreachable"
            status = api_status
            if active:
                status += f"  |  {len(active)} active job(s)"
            self._rebuild_batch_menu(jobs[:6])
            self._rebuild_hf_menu(hfs)
        else:
            status = state
            self._rebuild_batch_menu([])
            self._rebuild_hf_menu([])

        self._act_status.setText(f"Status: {status}")
        self.setToolTip(f"PixelPivot — {status}")

    def _fetch_api(self) -> None:
        """Background thread: refresh API cache. CPython dict assignment is atomic."""
        self._api_cache = {
            "health": _api.health(),
            "jobs":   _api.batch_history(),
            "hfs":    _api.hotfolders(),
        }

    # ------------------------------------------------------------------
    # Dynamic submenus
    # ------------------------------------------------------------------

    def _rebuild_batch_menu(self, jobs: list) -> None:
        self._batch_menu.clear()
        for job in jobs:
            run_id = job.get("run_id", "?")
            status = job.get("status", "?")
            pct    = job.get("progress")
            label  = f"#{run_id}: {status}" + (f" ({pct}%)" if pct is not None else "")
            sub    = self._batch_menu.addMenu(label)
            if status == "running":
                sub.addAction("Pause").triggered.connect(
                    lambda _, r=run_id: self._batch_control(r, "pause")
                )
                sub.addAction("Stop").triggered.connect(
                    lambda _, r=run_id: self._batch_control(r, "stop")
                )
            elif status == "paused":
                sub.addAction("Resume").triggered.connect(
                    lambda _, r=run_id: self._batch_control(r, "resume")
                )
                sub.addAction("Stop").triggered.connect(
                    lambda _, r=run_id: self._batch_control(r, "stop")
                )
            elif status in ("completed", "failed", "interrupted"):
                sub.addAction("Restart").triggered.connect(
                    lambda _, r=run_id: self._batch_restart(r)
                )
        if jobs:
            self._batch_menu.addSeparator()
        self._batch_menu.addAction("Start New Batch...").triggered.connect(self._show_start_batch)

    def _rebuild_hf_menu(self, watchers: list) -> None:
        self._hf_menu.clear()
        for w in watchers:
            wid   = w.get("id") or w.get("watcher_id", "?")
            src   = w.get("source_dir", str(wid))
            label = Path(src).name or src
            sub   = self._hf_menu.addMenu(label)
            sub.addAction("Unregister").triggered.connect(
                lambda _, i=wid: self._hf_unregister(i)
            )
        if watchers:
            self._hf_menu.addSeparator()
        self._hf_menu.addAction("Register Hot Folder...").triggered.connect(self._show_register_hf)

    # ------------------------------------------------------------------
    # Service operations
    # ------------------------------------------------------------------

    def _svc_start(self) -> None:
        try:
            if elevation.is_admin():
                scm.start_service()
            else:
                elevation.elevate(str(self._service_exe), "start")
        except Exception as e:
            QMessageBox.critical(None, "Service Error", f"Failed to start service:\n{e}")

    def _svc_stop(self) -> None:
        try:
            if elevation.is_admin():
                scm.stop_service()
            else:
                elevation.elevate(str(self._service_exe), "stop")
        except Exception as e:
            QMessageBox.critical(None, "Service Error", f"Failed to stop service:\n{e}")

    def _svc_install(self) -> None:
        try:
            elevation.elevate(str(self._service_exe), "install", "auto")
        except Exception as e:
            QMessageBox.critical(None, "Service Error", f"Failed to install service:\n{e}")

    def _svc_uninstall(self) -> None:
        try:
            elevation.elevate(str(self._service_exe), "remove")
        except Exception as e:
            QMessageBox.critical(None, "Service Error", f"Failed to uninstall service:\n{e}")

    # ------------------------------------------------------------------
    # Batch operations
    # ------------------------------------------------------------------

    def _batch_control(self, run_id: int, action: str) -> None:
        if _api.batch_control(run_id, action) is None:
            QMessageBox.warning(None, "Error", f"Failed to {action} batch #{run_id}.")
        self._update_state()

    def _batch_restart(self, run_id: int) -> None:
        if _api.batch_restart(run_id) is None:
            QMessageBox.warning(None, "Error", f"Failed to restart batch #{run_id}.")
        else:
            self.showMessage("PixelPivot", f"Batch #{run_id} queued for restart.",
                             QSystemTrayIcon.MessageIcon.Information, 3000)
        self._update_state()

    def _show_start_batch(self) -> None:
        dlg = StartBatchDialog()
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.payload:
            result = _api.batch_start(dlg.payload)
            if result:
                self.showMessage("PixelPivot", f"Batch #{result.get('run_id', '?')} queued.",
                                 QSystemTrayIcon.MessageIcon.Information, 3000)
            else:
                QMessageBox.warning(None, "Error", "Failed to start batch. Is the service running?")
            self._update_state()

    # ------------------------------------------------------------------
    # Hot folder operations
    # ------------------------------------------------------------------

    def _hf_unregister(self, watcher_id: Any) -> None:
        if _api.hotfolder_delete(watcher_id) is None:
            QMessageBox.warning(None, "Error", f"Failed to unregister watcher {watcher_id}.")
        else:
            self.showMessage("PixelPivot", "Hot folder unregistered.",
                             QSystemTrayIcon.MessageIcon.Information, 2000)
        self._update_state()

    def _show_register_hf(self) -> None:
        dlg = RegisterHotFolderDialog()
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.payload:
            result = _api.hotfolder_register(dlg.payload)
            if result:
                self.showMessage("PixelPivot", "Hot folder registered.",
                                 QSystemTrayIcon.MessageIcon.Information, 2000)
            else:
                QMessageBox.warning(None, "Error", "Failed to register. Is the service running?")
            self._update_state()

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------

    def _show_calibrate(self) -> None:
        dlg = CalibrateDialog()
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.payload:
            result = _api.calibrate(dlg.payload)
            if result:
                self.showMessage("PixelPivot", "Calibration run queued.",
                                 QSystemTrayIcon.MessageIcon.Information, 3000)
            else:
                QMessageBox.warning(None, "Error", "Failed to start calibration. Is the service running?")

    # ------------------------------------------------------------------
    # Settings & logs
    # ------------------------------------------------------------------

    def _show_settings(self) -> None:
        SettingsDialog(self._settings).exec()

    def _show_logs(self) -> None:
        if self._log_window is None or not self._log_window.isVisible():
            self._log_window = LogWindow(self._log_dir)
            self._log_window.show()
        else:
            self._log_window.raise_()
            self._log_window.activateWindow()
