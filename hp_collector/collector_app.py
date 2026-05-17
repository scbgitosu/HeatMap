"""
HP field collector — click on floorplan to trigger Wi-Fi scans.

Usage:
    python3 hp_collector/collector_app.py --project survey_projects/apartment_test
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _autodetect_qt_platform() -> Optional[str]:
    """Pick a Qt platform plugin matching the current display server.

    Mirrors scripts/run_collector.sh so running the module directly still works.
    Returns None if no graphical session is detectable.
    """
    wayland_display = os.environ.get("WAYLAND_DISPLAY")
    if wayland_display:
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        sock = Path(runtime_dir) / wayland_display
        if sock.exists():
            return "wayland"
    if os.environ.get("DISPLAY"):
        return "xcb"
    return None


_QT_HELP_MSG = """
Qt could not initialise a platform plugin. On Ubuntu this usually means
the runtime libs are missing or you are running outside a graphical session.

Try the seamless launcher (auto-picks wayland/xcb and falls back):

    ./scripts/run_collector.sh --project survey_projects/apartment_test

If it still fails, install the required system packages:

    sudo apt install network-manager iw \\
        qtwayland5 libxcb-cursor0 libxkbcommon-x11-0 libxcb-xinerama0

For a verbose diagnostic, re-run with QT_DEBUG_PLUGINS=1.
"""

from PyQt5.QtCore import Qt, QThread, pyqtSignal, QPointF, QRectF
from PyQt5.QtGui import (
    QColor, QFont, QPainter, QPen, QPixmap, QBrush,
)
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QDoubleSpinBox, QGraphicsEllipseItem, QGraphicsPixmapItem,
    QGraphicsScene, QGraphicsTextItem, QGraphicsView,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QSpinBox,
    QTextEdit, QVBoxLayout, QWidget,
)

from hp_collector.config_loader import load_project
from hp_collector.data_writer import DataWriter
from hp_collector.preflight import PreflightResult, run_preflight
from hp_collector.wifi_scan import (
    classify_scan_batch,
    collect_samples,
    list_wifi_interfaces,
    summarize,
)
from shared.session_cleanup import clean_session_folder, count_foreign_rows
from shared.survey_metrics import load_measurements
from shared.utils import generate_click_id, infer_room, now_iso, project_paths


def rssi_to_color(rssi: Optional[float]) -> QColor:
    """Map RSSI dBm to a color: green (strong) → red (weak)."""
    if rssi is None:
        return QColor(128, 128, 128)  # grey for unknown
    if rssi >= -50:
        return QColor(0, 200, 0)
    if rssi >= -60:
        return QColor(100, 200, 0)
    if rssi >= -67:
        return QColor(200, 200, 0)
    if rssi >= -70:
        return QColor(220, 140, 0)
    if rssi >= -75:
        return QColor(220, 60, 0)
    return QColor(200, 0, 0)


class ScanWorker(QThread):
    finished = pyqtSignal(list, dict)  # samples, summary
    error = pyqtSignal(str)

    def __init__(self, interface, ssid, bssid, samples, delay, click_context, backend):
        super().__init__()
        self.interface = interface
        self.ssid = ssid
        self.bssid = bssid or None
        self.samples = samples
        self.delay = delay
        self.click_context = click_context
        self.backend = backend

    def run(self):
        try:
            samples = collect_samples(
                interface=self.interface,
                ssid=self.ssid,
                bssid=self.bssid,
                samples=self.samples,
                delay_s=self.delay,
                click_context=self.click_context,
                backend=self.backend,
            )
            summary = summarize(samples, self.ssid, self.bssid)
            self.finished.emit(samples, summary)
        except Exception as e:
            self.error.emit(str(e))


class FloorplanView(QGraphicsView):
    point_clicked = pyqtSignal(float, float)

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self._scanning = False

    def set_scanning(self, scanning: bool):
        self._scanning = scanning
        self.setCursor(Qt.WaitCursor if scanning else Qt.CrossCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and not self._scanning:
            scene_pos = self.mapToScene(event.pos())
            self.point_clicked.emit(scene_pos.x(), scene_pos.y())
        super().mousePressEvent(event)

    def wheelEvent(self, event):
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale(factor, factor)


class NoteDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Note")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Note for last measurement point:"))
        self.text_edit = QTextEdit()
        layout.addWidget(self.text_edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_note(self) -> str:
        return self.text_edit.toPlainText().strip()


class CollectorWindow(QMainWindow):
    def __init__(self, project_dir: Path):
        super().__init__()
        self.project_dir = Path(project_dir)
        self.config, self.rooms, self.routers, self.metadata = load_project(project_dir)
        self.paths = project_paths(project_dir)
        self.waypoints = self._load_walk_waypoints()
        self._used_waypoint_ids: set[str] = set()
        self._waypoint_items: List[object] = []

        self._click_dots: List[dict] = []  # {dot, summary_row}
        self._summary_rows: List[dict] = []
        self._raw_rows: List[dict] = []
        self._loaded_session_id: Optional[str] = None
        self._scan_worker: Optional[ScanWorker] = None
        self._pending_dot = None
        self._preflight_ok = False

        self.setWindowTitle(f"Wi-Fi Collector — {self.config.project_name}")
        self._build_ui()
        self._reload_session()
        self._run_preflight(show_dialog_on_fail=True)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)

        # --- Left sidebar ---
        sidebar = QWidget()
        sidebar.setFixedWidth(260)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setAlignment(Qt.AlignTop)

        def label(text):
            lbl = QLabel(text)
            lbl.setFont(QFont("", 9))
            return lbl

        # Project name
        grp = QGroupBox("Project")
        gl = QVBoxLayout(grp)
        self.project_label = QLabel(self.config.project_name)
        self.project_label.setFont(QFont("", 10, QFont.Bold))
        gl.addWidget(self.project_label)
        sidebar_layout.addWidget(grp)

        # Session
        grp2 = QGroupBox("Session")
        gl2 = QVBoxLayout(grp2)
        gl2.addWidget(label("Session name:"))
        session_row = QHBoxLayout()
        self.session_edit = QLineEdit("baseline_current_router")
        session_row.addWidget(self.session_edit)
        self.session_reload_btn = QPushButton("Load")
        self.session_reload_btn.setToolTip(
            "Reload measurement dots and walk progress for this session folder"
        )
        self.session_reload_btn.clicked.connect(self._reload_session)
        session_row.addWidget(self.session_reload_btn)
        self.session_clean_btn = QPushButton("Clean")
        self.session_clean_btn.setToolTip(
            "Remove rows from other sessions and fix missing waypoint IDs"
        )
        self.session_clean_btn.clicked.connect(self._clean_session)
        session_row.addWidget(self.session_clean_btn)
        gl2.addLayout(session_row)
        self.session_edit.editingFinished.connect(self._on_session_name_changed)
        sidebar_layout.addWidget(grp2)

        # Router position
        grp3 = QGroupBox("Router position")
        gl3 = QVBoxLayout(grp3)
        self.router_combo = QComboBox()
        for rp in self.routers:
            self.router_combo.addItem(rp.name, rp.router_position_id)
        gl3.addWidget(self.router_combo)
        sidebar_layout.addWidget(grp3)

        # Wi-Fi settings
        grp4 = QGroupBox("Wi-Fi")
        gl4 = QVBoxLayout(grp4)
        gl4.addWidget(label("Target SSID:"))
        self.ssid_edit = QLineEdit(self.config.target_ssid)
        gl4.addWidget(self.ssid_edit)
        gl4.addWidget(label("Target BSSID (optional):"))
        self.bssid_edit = QLineEdit(self.config.target_bssid)
        gl4.addWidget(self.bssid_edit)
        gl4.addWidget(label("Wi-Fi interface:"))
        self.iface_combo = QComboBox()
        ifaces = list_wifi_interfaces()
        if self.config.default_interface:
            if self.config.default_interface not in ifaces:
                ifaces.insert(0, self.config.default_interface)
        for iface in ifaces:
            self.iface_combo.addItem(iface)
        if not ifaces:
            self.iface_combo.addItem("wlan0")
        gl4.addWidget(self.iface_combo)
        gl4.addWidget(label("Scan backend:"))
        self.backend_combo = QComboBox()
        for backend in ("iw", "auto", "nmcli"):
            self.backend_combo.addItem(backend)
        backend_default = getattr(self.config, "scan_backend", "iw") or "iw"
        if backend_default in ("iw", "auto", "nmcli"):
            self.backend_combo.setCurrentText(backend_default)
        gl4.addWidget(self.backend_combo)
        self._update_iface_combo_style()
        self.recheck_btn = QPushButton("Re-check Wi-Fi")
        self.recheck_btn.clicked.connect(self._on_recheck_wifi)
        gl4.addWidget(self.recheck_btn)
        sidebar_layout.addWidget(grp4)

        # Scan settings
        grp5 = QGroupBox("Scan settings")
        gl5 = QVBoxLayout(grp5)
        gl5.addWidget(label("Samples per click:"))
        self.samples_spin = QSpinBox()
        self.samples_spin.setRange(1, 50)
        self.samples_spin.setValue(10)
        gl5.addWidget(self.samples_spin)
        gl5.addWidget(label("Height (ft):"))
        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(0, 20)
        self.height_spin.setValue(4.0)
        self.height_spin.setSingleStep(0.5)
        gl5.addWidget(self.height_spin)
        self.guided_walk_checkbox = QCheckBox("Guided walk waypoint snap")
        self.guided_walk_checkbox.setEnabled(bool(self.waypoints))
        self.guided_walk_checkbox.setChecked(bool(self.waypoints))
        self.guided_walk_checkbox.toggled.connect(self._on_guided_walk_toggled)
        gl5.addWidget(self.guided_walk_checkbox)
        self.next_waypoint_label = QLabel("")
        self.next_waypoint_label.setWordWrap(True)
        self.next_waypoint_label.setStyleSheet("color: #1565c0;")
        gl5.addWidget(self.next_waypoint_label)
        sidebar_layout.addWidget(grp5)

        # Status banner
        self.status_label = QLabel("Ready")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setFont(QFont("", 11, QFont.Bold))
        self.status_label.setFixedHeight(36)
        self._set_status("ready")
        sidebar_layout.addWidget(self.status_label)

        # Buttons
        self.undo_btn = QPushButton("Undo last point")
        self.undo_btn.clicked.connect(self._undo_last)
        sidebar_layout.addWidget(self.undo_btn)

        self.note_btn = QPushButton("Add note to last point")
        self.note_btn.clicked.connect(self._add_note)
        sidebar_layout.addWidget(self.note_btn)

        quit_btn = QPushButton("Quit")
        quit_btn.clicked.connect(self.close)
        sidebar_layout.addWidget(quit_btn)

        sidebar_layout.addStretch()
        main_layout.addWidget(sidebar)

        # --- Right canvas ---
        self.scene = QGraphicsScene()
        self.view = FloorplanView(self.scene)
        self.view.point_clicked.connect(self._on_click)

        pixmap = QPixmap(str(self.paths["floorplan_png"]))
        self._floorplan_item = QGraphicsPixmapItem(pixmap)
        self.scene.addItem(self._floorplan_item)
        self._img_w = pixmap.width()
        self._img_h = pixmap.height()

        self._draw_room_overlays()
        self._draw_router_overlays()
        self._draw_waypoint_overlays()

        main_layout.addWidget(self.view)
        self.resize(1100, 700)

    def _wifi_settings(self) -> tuple:
        iface = self.iface_combo.currentText().strip()
        ssid = self.ssid_edit.text().strip()
        bssid = self.bssid_edit.text().strip() or None
        backend = self.backend_combo.currentText().strip() or "iw"
        return iface, ssid, bssid, backend

    def _update_iface_combo_style(self):
        iface = self.iface_combo.currentText().strip()
        live = list_wifi_interfaces()
        if iface and live and iface not in live:
            self.iface_combo.setStyleSheet("QComboBox { color: #b71c1c; }")
        else:
            self.iface_combo.setStyleSheet("")

    def _apply_preflight_state(self, result: PreflightResult):
        self._preflight_ok = result.ok
        self.view.setEnabled(result.ok)
        self._update_iface_combo_style()
        if result.ok:
            self._set_status("wifi_ready")
        else:
            self._set_status("blocked")

    def _show_preflight_dialog(self, result: PreflightResult):
        QMessageBox.warning(
            self,
            "Wi-Fi preflight failed",
            result.message(),
        )

    def _run_preflight(self, show_dialog_on_fail: bool = False) -> PreflightResult:
        iface, ssid, bssid, backend = self._wifi_settings()
        result = run_preflight(iface, ssid, bssid, backend=backend)
        self._apply_preflight_state(result)
        if not result.ok and show_dialog_on_fail:
            self._show_preflight_dialog(result)
        return result

    def _on_recheck_wifi(self):
        result = self._run_preflight(show_dialog_on_fail=True)
        if result.ok:
            QMessageBox.information(self, "Wi-Fi preflight", "Wi-Fi is ready for surveying.")

    def _set_status(self, state: str, msg: str = ""):
        colors = {
            "ready": ("#e8f5e9", "#2e7d32"),
            "wifi_ready": ("#e8f5e9", "#2e7d32"),
            "scanning": ("#fff8e1", "#f57f17"),
            "saved": ("#e3f2fd", "#1565c0"),
            "warning": ("#fff8e1", "#e65100"),
            "blocked": ("#ffebee", "#b71c1c"),
            "error": ("#ffebee", "#b71c1c"),
        }
        bg, fg = colors.get(state, ("#fff", "#000"))
        labels = {
            "ready": msg or "Ready",
            "wifi_ready": "Wi-Fi ready",
            "scanning": "Scanning…",
            "saved": "Saved",
            "warning": msg or "Partial scan",
            "blocked": "Wi-Fi blocked — fix and Re-check",
            "error": f"Error: {msg}",
        }
        self.status_label.setText(labels.get(state, state))
        self.status_label.setStyleSheet(
            f"background-color: {bg}; color: {fg}; border-radius: 4px; padding: 4px;"
        )

    def _draw_room_overlays(self):
        from PyQt5.QtGui import QPolygonF
        from PyQt5.QtCore import QPointF
        for room in self.rooms:
            if not room.polygon:
                continue
            poly = QPolygonF([QPointF(x, y) for x, y in room.polygon])
            item = self.scene.addPolygon(
                poly,
                QPen(QColor(100, 100, 200, 150), 1.5),
                QBrush(QColor(100, 100, 200, 30)),
            )
            if room.label_x is not None and room.label_y is not None:
                txt = self.scene.addText(room.room_name)
                txt.setDefaultTextColor(QColor(50, 50, 150))
                txt.setPos(room.label_x, room.label_y)

    def _draw_router_overlays(self):
        for rp in self.routers:
            r = 8
            dot = self.scene.addEllipse(
                rp.x_px - r, rp.y_px - r, r * 2, r * 2,
                QPen(QColor(255, 165, 0), 2),
                QBrush(QColor(255, 165, 0, 120)),
            )
            txt = self.scene.addText(f"★ {rp.name}")
            txt.setDefaultTextColor(QColor(200, 100, 0))
            txt.setPos(rp.x_px + r + 2, rp.y_px - 8)

    def _draw_waypoint_overlays(self):
        self._refresh_waypoint_overlays()

    def _refresh_waypoint_overlays(self):
        if not hasattr(self, "scene"):
            return
        for item in self._waypoint_items:
            self.scene.removeItem(item)
        self._waypoint_items = []

        if not self.waypoints:
            self._update_next_waypoint_hint()
            return

        guided = self.guided_walk_checkbox.isChecked()
        next_waypoint = self._next_waypoint() if guided else None
        next_waypoint_id = next_waypoint.get("waypoint_id", "") if next_waypoint else ""

        for waypoint in self.waypoints:
            waypoint_id = waypoint.get("waypoint_id", "")
            x = float(waypoint.get("x_px", 0))
            y = float(waypoint.get("y_px", 0))
            order = waypoint.get("order", "")
            label = self._waypoint_display_label(waypoint)
            is_done = waypoint_id in self._used_waypoint_ids
            is_next = bool(next_waypoint_id and waypoint_id == next_waypoint_id)

            if is_next:
                r = 12
                pen = QPen(QColor(0, 160, 255), 3)
                brush = QBrush(QColor(0, 160, 255, 35))
                text = f"NEXT {label}"
                text_color = QColor(0, 110, 200)
            elif is_done:
                r = 7
                pen = QPen(QColor(120, 120, 120), 1.5)
                brush = QBrush(QColor(120, 120, 120, 110))
                text = str(order or waypoint_id)
                text_color = QColor(110, 110, 110)
            else:
                r = 7
                pen = QPen(QColor(0, 160, 255), 1.8)
                brush = QBrush(QColor(0, 160, 255, 25))
                text = str(order or waypoint_id)
                text_color = QColor(0, 120, 200)

            dot = self.scene.addEllipse(x - r, y - r, r * 2, r * 2, pen, brush)
            dot.setZValue(5)
            txt = self.scene.addText(text)
            txt.setDefaultTextColor(text_color)
            txt.setPos(x + r + 3, y - r - 2)
            txt.setZValue(5)
            self._waypoint_items.extend([dot, txt])

        self._update_next_waypoint_hint()

    def _data_writer(self) -> DataWriter:
        session_id = self.session_edit.text().strip() or "session"
        session_dir = self.paths["survey_sessions_dir"] / session_id
        return DataWriter(session_dir)

    def _load_walk_waypoints(self) -> List[dict]:
        path = self.paths.get("walk_waypoints_json")
        if not path or not path.exists():
            return []
        try:
            with open(path, encoding="utf-8") as f:
                return sorted(json.load(f), key=lambda w: int(w.get("order", 0)))
        except Exception:
            return []

    def _waypoint_display_label(self, waypoint: dict) -> str:
        waypoint_id = waypoint.get("waypoint_id", "")
        label = waypoint.get("label", "")
        if label:
            return f"{waypoint_id} ({label})" if waypoint_id else label
        return waypoint_id

    def _sync_used_waypoints(self):
        self._used_waypoint_ids = {
            row["waypoint_id"]
            for row in self._summary_rows
            if row.get("waypoint_id")
        }

    def _next_waypoint(self) -> Optional[dict]:
        for waypoint in self.waypoints:
            waypoint_id = waypoint.get("waypoint_id", "")
            if waypoint_id and waypoint_id not in self._used_waypoint_ids:
                return waypoint
        return None

    def _update_next_waypoint_hint(self):
        if not hasattr(self, "next_waypoint_label"):
            return
        if not self.waypoints:
            self.next_waypoint_label.setText("")
            return

        total = len(self.waypoints)
        completed = len(self._used_waypoint_ids)
        if not self.guided_walk_checkbox.isChecked():
            self.next_waypoint_label.setText(
                f"Waypoints visible ({completed}/{total}); snap is off."
            )
            return

        waypoint = self._next_waypoint()
        if waypoint:
            self.next_waypoint_label.setText(
                f"Next: {self._waypoint_display_label(waypoint)} "
                f"({completed}/{total} done)"
            )
        else:
            self.next_waypoint_label.setText(f"Walk complete ({completed}/{total})")

    def _on_guided_walk_toggled(self, _checked: bool):
        self._refresh_waypoint_overlays()

    def _snap_to_waypoint(self, x: float, y: float, tolerance_px: float = 40.0) -> tuple[float, float, str, str]:
        if not self.waypoints or not self.guided_walk_checkbox.isChecked():
            return x, y, "", ""
        waypoint = self._next_waypoint()
        if not waypoint:
            return x, y, "", ""

        dx = float(waypoint.get("x_px", 0)) - x
        dy = float(waypoint.get("y_px", 0)) - y
        dist = (dx * dx + dy * dy) ** 0.5
        if dist > tolerance_px:
            return x, y, "", ""

        waypoint_id = waypoint.get("waypoint_id", "")
        self._used_waypoint_ids.add(waypoint_id)
        self._refresh_waypoint_overlays()
        return (
            float(waypoint.get("x_px", x)),
            float(waypoint.get("y_px", y)),
            waypoint_id,
            waypoint.get("label", ""),
        )

    def _current_session_id(self) -> str:
        return self.session_edit.text().strip() or "session"

    def _clear_session_display(self):
        """Remove measurement dots and in-memory rows for the active session view."""
        if self._pending_dot:
            self.scene.removeItem(self._pending_dot)
            self._pending_dot = None
        for entry in self._click_dots:
            self.scene.removeItem(entry["dot"])
        self._click_dots = []
        self._summary_rows = []
        self._raw_rows = []
        self._used_waypoint_ids = set()

    def _clean_session(self):
        session_id = self._current_session_id()
        if self._scan_worker and self._scan_worker.isRunning():
            QMessageBox.warning(self, "Scan in progress", "Wait for the current scan to finish.")
            return
        report = clean_session_folder(
            self._data_writer().session_dir,
            waypoints_json=self.paths.get("walk_waypoints_json"),
        )
        QMessageBox.information(
            self,
            "Session cleaned",
            f"{session_id}: {report.summary_before} → {report.summary_after} points\n"
            f"Removed {report.removed_foreign_session} foreign row(s), "
            f"{report.removed_off_path} off-path click(s).\n"
            f"Backfilled {report.backfilled_waypoint_id} waypoint ID(s).",
        )
        self._reload_session()

    def _reload_session(self):
        """Switch the floorplan view to match the session name in the sidebar."""
        session_id = self._current_session_id()
        self._clear_session_display()
        try:
            dw = self._data_writer()
            foreign = count_foreign_rows(dw.session_dir)
            if foreign:
                self._set_status(
                    "warning",
                    f"{foreign} row(s) from other sessions — use Clean or fix session name",
                )
            rows = load_measurements(dw.summary_path, session_id=session_id)
            click_ids = {r.get("click_id") for r in rows if r.get("click_id")}
            raw_rows = [
                r for r in dw.load_raw_rows()
                if r.get("session_id") == session_id and r.get("click_id") in click_ids
            ]
            self._summary_rows = rows
            self._raw_rows = raw_rows
            for row in rows:
                try:
                    x = float(row["x_px"])
                    y = float(row["y_px"])
                    rssi = float(row["rssi_avg_dbm"]) if row.get("rssi_avg_dbm") else None
                    dot = self._add_dot(x, y, rssi, confirmed=True)
                    self._click_dots.append({"dot": dot, "summary_row": row})
                except (ValueError, KeyError):
                    pass
        except Exception:
            pass
        self._loaded_session_id = session_id
        self._sync_used_waypoints()
        self._refresh_waypoint_overlays()
        n = len(self._summary_rows)
        if n:
            self._set_status("ready", f"Loaded {n} point(s) — {session_id}")
        else:
            self._set_status("ready", f"New session — {session_id}")

    def _on_session_name_changed(self):
        session_id = self._current_session_id()
        if session_id == self._loaded_session_id:
            return
        if self._scan_worker and self._scan_worker.isRunning():
            QMessageBox.warning(
                self,
                "Scan in progress",
                "Wait for the current scan to finish before changing session.",
            )
            if self._loaded_session_id:
                self.session_edit.setText(self._loaded_session_id)
            return
        self._reload_session()

    def _ensure_session_loaded(self):
        if self._current_session_id() != self._loaded_session_id:
            self._reload_session()

    def _on_click(self, x: float, y: float):
        if not self._preflight_ok:
            self._run_preflight(show_dialog_on_fail=True)
            return
        if self._scan_worker and self._scan_worker.isRunning():
            return

        self._ensure_session_loaded()

        x, y, waypoint_id, waypoint_label = self._snap_to_waypoint(x, y)
        session_id = self._current_session_id()
        dw = self._data_writer()
        click_id = dw.next_click_id(session_id)

        room = infer_room(x, y, self.rooms)
        room_id = room.room_id if room else "unknown"
        room_name = room.room_name if room else "unknown"

        router_pos_id = self.router_combo.currentData() or ""
        height = self.height_spin.value()

        click_context = {
            "click_id": click_id,
            "session_id": session_id,
            "router_position_id": router_pos_id,
            "x_px": x,
            "y_px": y,
            "room_id": room_id,
            "room_name": room_name,
            "waypoint_id": waypoint_id,
            "height_ft": height,
        }
        if waypoint_id:
            click_context["note"] = f"waypoint:{waypoint_id}:{waypoint_label}"

        # Draw pending dot immediately
        self._pending_dot = self._add_dot(x, y, rssi=None, confirmed=False)

        self._set_status("scanning")
        self.view.set_scanning(True)

        iface = self.iface_combo.currentText()
        ssid = self.ssid_edit.text().strip()
        bssid = self.bssid_edit.text().strip() or None
        backend = self.backend_combo.currentText().strip() or "iw"
        n_samples = self.samples_spin.value()

        self._current_context = click_context
        self._scan_worker = ScanWorker(iface, ssid, bssid, n_samples, 0.5, click_context, backend)
        self._scan_worker.finished.connect(self._on_scan_done)
        self._scan_worker.error.connect(self._on_scan_error)
        self._scan_worker.start()

    def _add_dot(self, x, y, rssi, confirmed: bool) -> QGraphicsEllipseItem:
        r = 8
        color = rssi_to_color(rssi)
        border_color = QColor(50, 50, 50) if confirmed else QColor(200, 200, 200)
        dot = self.scene.addEllipse(
            x - r, y - r, r * 2, r * 2,
            QPen(border_color, 1.5),
            QBrush(color if confirmed else QColor(200, 200, 200, 150)),
        )
        dot.setZValue(10)
        return dot

    def _on_scan_done(self, samples, summary):
        self.view.set_scanning(False)

        ssid = self.ssid_edit.text().strip()
        bssid = self.bssid_edit.text().strip() or None
        outcome = classify_scan_batch(samples, ssid, bssid)

        if outcome.status == "failed":
            if self._pending_dot:
                self.scene.removeItem(self._pending_dot)
                self._pending_dot = None
            pending_waypoint_id = getattr(self, "_current_context", {}).get("waypoint_id", "")
            if pending_waypoint_id:
                self._used_waypoint_ids.discard(pending_waypoint_id)
                self._refresh_waypoint_overlays()
            self._set_status("error", outcome.error_message)
            if outcome.error_message and "Network is down" in outcome.error_message:
                self._preflight_ok = False
                self.view.setEnabled(False)
            return

        if outcome.status == "partial":
            summary["note"] = (
                f"partial_scan:{outcome.failed_count}/{outcome.total_scans} failed"
            )

        if self._pending_dot:
            rssi = summary.get("rssi_avg_dbm")
            color = rssi_to_color(rssi)
            self._pending_dot.setBrush(QBrush(color))
            self._pending_dot.setPen(QPen(QColor(50, 50, 50), 1.5))

        dw = self._data_writer()
        dw.append_raw(samples)
        dw.append_summary(summary)

        self._summary_rows.append(summary)
        self._raw_rows.extend([s.to_dict() for s in samples])
        self._click_dots.append({"dot": self._pending_dot, "summary_row": summary})
        self._pending_dot = None
        self._sync_used_waypoints()
        self._refresh_waypoint_overlays()

        rssi = summary.get("rssi_avg_dbm")
        rssi_str = f"{rssi:.1f} dBm" if rssi is not None else "N/A"
        snr = summary.get("snr_avg_db")
        tx = summary.get("tx_bitrate_avg_mbps")
        last_parts = [rssi_str]
        if snr is not None:
            last_parts.append(f"SNR {snr:.1f} dB")
        if tx is not None:
            last_parts.append(f"TX {tx:.0f} Mbps")
        if outcome.status == "partial":
            self._set_status("warning", summary["note"])
        else:
            self._set_status("saved")
        self.setWindowTitle(
            f"Wi-Fi Collector — {self.config.project_name} | Last: {' / '.join(last_parts)}"
        )

    def _on_scan_error(self, msg: str):
        self.view.set_scanning(False)
        if self._pending_dot:
            self.scene.removeItem(self._pending_dot)
            self._pending_dot = None
        pending_waypoint_id = getattr(self, "_current_context", {}).get("waypoint_id", "")
        if pending_waypoint_id:
            self._used_waypoint_ids.discard(pending_waypoint_id)
            self._refresh_waypoint_overlays()
        self._set_status("error", msg)

    def _undo_last(self):
        if not self._click_dots:
            QMessageBox.information(self, "Undo", "No points to undo.")
            return
        last = self._click_dots.pop()
        self.scene.removeItem(last["dot"])

        removed_click_id = last["summary_row"].get("click_id", "")
        self._summary_rows = [r for r in self._summary_rows if r.get("click_id") != removed_click_id]
        self._raw_rows = [r for r in self._raw_rows if r.get("click_id") != removed_click_id]

        dw = self._data_writer()
        dw.rewrite_summary(self._summary_rows)
        dw.rewrite_raw(self._raw_rows)
        self._sync_used_waypoints()
        self._refresh_waypoint_overlays()
        self._set_status("ready")

    def _add_note(self):
        if not self._click_dots:
            QMessageBox.information(self, "Note", "No points recorded yet.")
            return
        dlg = NoteDialog(self)
        if dlg.exec_() == QDialog.Accepted:
            note = dlg.get_note()
            last_summary = self._click_dots[-1]["summary_row"]
            last_summary["note"] = note

            last_click_id = last_summary.get("click_id", "")
            for row in self._summary_rows:
                if row.get("click_id") == last_click_id:
                    row["note"] = note

            dw = self._data_writer()
            dw.rewrite_summary(self._summary_rows)


def main():
    parser = argparse.ArgumentParser(description="Wi-Fi apartment survey collector")
    parser.add_argument("--project", required=True, help="Path to project directory")
    args = parser.parse_args()

    if not os.environ.get("QT_QPA_PLATFORM"):
        detected = _autodetect_qt_platform()
        if detected:
            os.environ["QT_QPA_PLATFORM"] = detected
            print(f"[collector] QT_QPA_PLATFORM auto-set to '{detected}'", file=sys.stderr)
        else:
            print(
                "[collector] No graphical session detected "
                "(WAYLAND_DISPLAY and DISPLAY both empty).",
                file=sys.stderr,
            )
            print(_QT_HELP_MSG, file=sys.stderr)
            sys.exit(1)

    try:
        app = QApplication(sys.argv)
    except Exception as e:
        print(f"[collector] Failed to start Qt: {e}", file=sys.stderr)
        print(_QT_HELP_MSG, file=sys.stderr)
        sys.exit(1)
    app.setStyle("Fusion")
    try:
        window = CollectorWindow(Path(args.project))
        window.show()
        sys.exit(app.exec_())
    except FileNotFoundError as e:
        from PyQt5.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Project Error", str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
