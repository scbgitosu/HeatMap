#!/usr/bin/env python3
"""Clean contaminated or incomplete survey session CSVs."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.session_cleanup import clean_session_folder
from shared.utils import project_paths


def main():
    parser = argparse.ArgumentParser(description="Clean survey session measurement CSVs")
    parser.add_argument("--project", required=True, help="Project directory")
    parser.add_argument("--session", required=True, help="Session folder name under survey_sessions/")
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not write .bak copies before overwriting",
    )
    parser.add_argument(
        "--keep-off-path",
        action="store_true",
        help="Keep clicks farther than 40px from any walk waypoint",
    )
    args = parser.parse_args()

    project_dir = Path(args.project)
    paths = project_paths(project_dir)
    session_dir = paths["survey_sessions_dir"] / args.session

    report = clean_session_folder(
        session_dir,
        waypoints_json=paths["walk_waypoints_json"],
        drop_off_path=not args.keep_off_path,
        backup=not args.no_backup,
    )

    print(f"Session: {report.session_id}")
    print(f"  summary: {report.summary_before} -> {report.summary_after} rows")
    print(f"  raw:     {report.raw_before} -> {report.raw_after} rows")
    print(f"  removed foreign session_id: {report.removed_foreign_session}")
    print(f"  removed off-path clicks:    {report.removed_off_path}")
    print(f"  backfilled waypoint_id:     {report.backfilled_waypoint_id}")
    for w in report.warnings:
        print(f"  warning: {w}")


if __name__ == "__main__":
    main()
