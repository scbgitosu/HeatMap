"""
HP field collector — click on floorplan to trigger Wi-Fi scans.

Usage:
    python3 hp_collector/collector_app.py --project survey_projects/apartment_test
"""
from __future__ import annotations

import argparse
import csv
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
    QApplication, QComboBox, QDialog, QDialogButtonBox,
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

    def __init__(self, interface, ssid, bssid, samples, delay, click_context):
        super().__init__()
        self.interface = interface
        self.ssid = ssid
        self.bssid = bssid or None
        self.samples = samples
        self.delay = delay
        self.click_context = click_context

    def run(self):
        try:
            samples = collect_samples(
                interface=self.interface,
                ssid=self.ssid,
                bssid=self.bssid,
                samples=self.samples,
                delay_s=self.delay,
                click_context=self.click_context,
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

        self._click_dots: List[dict] = []  # {dot, summary_row}
        self._summary_rows: List[dict] = []
        self._raw_rows: List[dict] = []
        self._scan_worker: Optional[ScanWorker] = None
        self._pending_dot = None
        self._preflight_ok = False

        self.setWindowTitle(f"Wi-Fi Collector — {self.config.project_name}")
        self._build_ui()
        self._load_existing_points()
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
        self.session_edit = QLineEdit("baseline_current_router")
        gl2.addWidget(self.session_edit)
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

        main_layout.addWidget(self.view)
        self.resize(1100, 700)

    def _wifi_settings(self) -> tuple:
        iface = self.iface_combo.currentText().strip()
        ssid = self.ssid_edit.text().strip()
        bssid = self.bssid_edit.text().strip() or None
        return iface, ssid, bssid

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
        iface, ssid, bssid = self._wifi_settings()
        result = run_preflight(iface, ssid, bssid)
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
            "ready": "Ready",
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

    def _data_writer(self) -> DataWriter:
        session_id = self.session_edit.text().strip() or "session"
        session_dir = self.paths["survey_sessions_dir"] / session_id
        return DataWriter(session_dir)

    def _load_existing_points(self):
        """Redraw survey points from existing summary CSV on launch."""
        try:
            dw = self._data_writer()
            rows = dw.load_summary_rows()
            raw_rows = dw.load_raw_rows()
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

    def _on_click(self, x: float, y: float):
        if not self._preflight_ok:
            self._run_preflight(show_dialog_on_fail=True)
            return
        if self._scan_worker and self._scan_worker.isRunning():
            return

        session_id = self.session_edit.text().strip() or "session"
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
            "height_ft": height,
        }

        # Draw pending dot immediately
        self._pending_dot = self._add_dot(x, y, rssi=None, confirmed=False)

        self._set_status("scanning")
        self.view.set_scanning(True)

        iface = self.iface_combo.currentText()
        ssid = self.ssid_edit.text().strip()
        bssid = self.bssid_edit.text().strip() or None
        n_samples = self.samples_spin.value()

        self._current_context = click_context
        self._scan_worker = ScanWorker(iface, ssid, bssid, n_samples, 0.5, click_context)
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

        rssi = summary.get("rssi_avg_dbm")
        rssi_str = f"{rssi:.1f} dBm" if rssi is not None else "N/A"
        if outcome.status == "partial":
            self._set_status("warning", summary["note"])
        else:
            self._set_status("saved")
        self.setWindowTitle(
            f"Wi-Fi Collector — {self.config.project_name} | Last: {rssi_str}"
        )

    def _on_scan_error(self, msg: str):
        self.view.set_scanning(False)
        if self._pending_dot:
            self.scene.removeItem(self._pending_dot)
            self._pending_dot = None
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
