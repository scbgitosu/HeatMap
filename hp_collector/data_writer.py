"""Append-safe CSV writer for raw and summary measurement data."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List

from shared.csv_schema import RAW_COLUMNS, SUMMARY_COLUMNS, open_raw_writer, open_summary_writer
from shared.models import Sample


class DataWriter:
    def __init__(self, session_dir: Path):
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.raw_path = self.session_dir / "measurements_raw.csv"
        self.summary_path = self.session_dir / "measurements_summary.csv"

    def append_raw(self, samples: List[Sample], click_context: dict = None):
        """Write sample rows to raw CSV, flushing after each write."""
        fh, writer = open_raw_writer(self.raw_path)
        try:
            for s in samples:
                row = s.to_dict()
                writer.writerow(row)
            fh.flush()
        finally:
            fh.close()

    def append_summary(self, summary_row: dict):
        """Write one summary row to summary CSV, flushing immediately."""
        fh, writer = open_summary_writer(self.summary_path)
        try:
            writer.writerow(summary_row)
            fh.flush()
        finally:
            fh.close()

    def next_click_id(self, session_id: str) -> str:
        """Scan existing summary CSV to find the max click number, return next ID."""
        if not self.summary_path.exists() or self.summary_path.stat().st_size == 0:
            return f"{session_id}_0001"

        max_n = 0
        prefix = f"{session_id}_"
        with open(self.summary_path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                click_id = row.get("click_id", "")
                if click_id.startswith(prefix):
                    try:
                        n = int(click_id[len(prefix):])
                        if n > max_n:
                            max_n = n
                    except ValueError:
                        pass

        return f"{session_id}_{max_n + 1:04d}"

    def load_summary_rows(self) -> List[dict]:
        """Load all existing summary rows (for crash recovery / undo)."""
        if not self.summary_path.exists() or self.summary_path.stat().st_size == 0:
            return []
        with open(self.summary_path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))

    def rewrite_summary(self, rows: List[dict]):
        """Overwrite summary CSV with given rows (used by undo)."""
        with open(self.summary_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=SUMMARY_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def rewrite_raw(self, rows: List[dict]):
        """Overwrite raw CSV with given rows (used by undo)."""
        with open(self.raw_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=RAW_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def load_raw_rows(self) -> List[dict]:
        """Load all existing raw rows."""
        if not self.raw_path.exists() or self.raw_path.stat().st_size == 0:
            return []
        with open(self.raw_path, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
