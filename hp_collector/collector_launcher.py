"""
Button-style launcher for the HP field collector.

Usage:
    python3 hp_collector/collector_launcher.py
    python3 hp_collector/collector_launcher.py --project survey_projects/apartment_test
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QProcess
from PyQt5.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT = "survey_projects/apartment_test"


class CollectorLauncher(QMainWindow):
    def __init__(self, project: str):
        super().__init__()
        self.setWindowTitle("Wi-Fi Survey Collector Launcher")
        self.process: QProcess | None = None
        self._build_ui(project)

    def _build_ui(self, project: str):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        layout.addWidget(QLabel("Project path:"))
        row = QHBoxLayout()
        self.project_edit = QLineEdit(project)
        row.addWidget(self.project_edit)
        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_project)
        row.addWidget(browse_btn)
        layout.addLayout(row)

        button_row = QHBoxLayout()
        preflight_btn = QPushButton("Run Preflight")
        preflight_btn.clicked.connect(self._run_preflight)
        button_row.addWidget(preflight_btn)

        launch_btn = QPushButton("Launch Collector")
        launch_btn.clicked.connect(self._launch_collector)
        button_row.addWidget(launch_btn)

        open_btn = QPushButton("Open Project Folder")
        open_btn.clicked.connect(self._open_project_folder)
        button_row.addWidget(open_btn)
        layout.addLayout(button_row)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(360)
        layout.addWidget(self.output)

        self.resize(820, 520)

    def _project(self) -> str:
        return self.project_edit.text().strip() or DEFAULT_PROJECT

    def _append(self, text: str):
        self.output.append(text.rstrip())

    def _browse_project(self):
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Choose survey project",
            str((REPO_ROOT / self._project()).resolve()),
        )
        if chosen:
            self.project_edit.setText(chosen)

    def _run_preflight(self):
        project = self._project()
        cmd = [sys.executable, "hp_collector/preflight.py", "--project", project]
        self._append(f"$ {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        except Exception as e:
            self._append(f"ERROR: {e}")
            return
        if result.stdout:
            self._append(result.stdout)
        if result.stderr:
            self._append(result.stderr)
        if result.returncode == 0:
            QMessageBox.information(self, "Preflight", "Wi-Fi preflight passed.")
        else:
            QMessageBox.warning(self, "Preflight", "Wi-Fi preflight failed. See output.")

    def _launch_collector(self):
        if self.process and self.process.state() != QProcess.NotRunning:
            QMessageBox.information(self, "Collector", "Collector is already running.")
            return

        project = self._project()
        script = REPO_ROOT / "scripts" / "run_collector.sh"
        self.process = QProcess(self)
        self.process.setWorkingDirectory(str(REPO_ROOT))
        self.process.setProgram(str(script))
        self.process.setArguments(["--project", project])
        self.process.readyReadStandardOutput.connect(self._read_stdout)
        self.process.readyReadStandardError.connect(self._read_stderr)
        self.process.finished.connect(self._collector_finished)
        self._append(f"$ {script} --project {project}")
        self.process.start()

    def _read_stdout(self):
        if self.process:
            self._append(bytes(self.process.readAllStandardOutput()).decode(errors="replace"))

    def _read_stderr(self):
        if self.process:
            self._append(bytes(self.process.readAllStandardError()).decode(errors="replace"))

    def _collector_finished(self, code: int, _status):
        self._append(f"[collector exited with code {code}]")

    def _open_project_folder(self):
        project_path = Path(self._project())
        if not project_path.is_absolute():
            project_path = REPO_ROOT / project_path
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", str(project_path)])
            else:
                subprocess.Popen(["xdg-open", str(project_path)])
        except Exception as e:
            self._append(f"ERROR: {e}")


def main():
    parser = argparse.ArgumentParser(description="Launch the HP Wi-Fi collector")
    parser.add_argument("--project", default=DEFAULT_PROJECT, help="Survey project directory")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    win = CollectorLauncher(args.project)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
