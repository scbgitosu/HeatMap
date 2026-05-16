import csv
import os
from pathlib import Path

RAW_COLUMNS = [
    "timestamp",
    "session_id",
    "router_position_id",
    "click_id",
    "x_px",
    "y_px",
    "room_id",
    "room_name",
    "height_ft",
    "ssid",
    "bssid",
    "frequency_mhz",
    "channel",
    "rssi_dbm",
    "interface",
    "scan_backend",
    "sample_number",
    "note",
]

SUMMARY_COLUMNS = [
    "timestamp_start",
    "timestamp_end",
    "session_id",
    "router_position_id",
    "click_id",
    "x_px",
    "y_px",
    "room_id",
    "room_name",
    "height_ft",
    "target_ssid",
    "target_bssid",
    "best_bssid",
    "frequency_mhz",
    "channel",
    "sample_count",
    "rssi_avg_dbm",
    "rssi_min_dbm",
    "rssi_max_dbm",
    "rssi_std_db",
    "missing_sample_count",
    "note",
]


def _open_csv_writer(path: Path, columns: list) -> tuple:
    """Return (file_handle, DictWriter). Writes header only if file is new/empty."""
    is_new = not path.exists() or path.stat().st_size == 0
    fh = open(path, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
    if is_new:
        writer.writeheader()
    return fh, writer


def open_raw_writer(path: Path) -> tuple:
    return _open_csv_writer(path, RAW_COLUMNS)


def open_summary_writer(path: Path) -> tuple:
    return _open_csv_writer(path, SUMMARY_COLUMNS)
