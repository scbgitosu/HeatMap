"""Load and validate project configuration files."""
from __future__ import annotations

import json
from pathlib import Path
from typing import List

from shared.models import ProjectConfig, RoomLabel, RouterPosition
from shared.utils import project_paths


def _require(path: Path, hint: str):
    if not path.exists():
        raise FileNotFoundError(
            f"Missing {path.name} — {hint}. Expected at: {path}"
        )


def load_project(project_dir: Path) -> tuple:
    """
    Load all project files. Returns (ProjectConfig, List[RoomLabel], List[RouterPosition], metadata).
    Raises FileNotFoundError with friendly messages for missing files.
    """
    paths = project_paths(Path(project_dir))

    _require(
        paths["project_config"],
        "run floorplan_labeler.py on the Mac first",
    )
    _require(
        paths["floorplan_png"],
        "run floorplan_import.py on the Mac first to generate a floorplan",
    )
    _require(
        paths["rooms_json"],
        "run floorplan_labeler.py on the Mac first to label rooms",
    )
    _require(
        paths["router_positions_json"],
        "run floorplan_labeler.py on the Mac to place router positions",
    )

    with open(paths["project_config"], encoding="utf-8") as f:
        cfg_data = json.load(f)

    config = ProjectConfig(
        project_name=cfg_data.get("project_name", ""),
        target_ssid=cfg_data.get("target_ssid", ""),
        target_bssid=cfg_data.get("target_bssid", ""),
        default_interface=cfg_data.get("default_interface", ""),
        units=cfg_data.get("units", "feet"),
        collection_mode=cfg_data.get("collection_mode", "click_to_scan"),
        scan_backend=cfg_data.get("scan_backend", "iw"),
        paths=cfg_data.get("paths", {}),
    )

    with open(paths["rooms_json"], encoding="utf-8") as f:
        rooms_data = json.load(f)

    rooms: List[RoomLabel] = []
    for r in rooms_data:
        rooms.append(RoomLabel(
            room_id=r.get("room_id", ""),
            room_name=r.get("room_name", ""),
            polygon=[tuple(p) for p in r.get("polygon", [])],
            label_x=r.get("label_x"),
            label_y=r.get("label_y"),
        ))

    with open(paths["router_positions_json"], encoding="utf-8") as f:
        router_data = json.load(f)

    routers: List[RouterPosition] = []
    for rp in router_data:
        routers.append(RouterPosition(
            router_position_id=rp.get("router_position_id", ""),
            name=rp.get("name", ""),
            x_px=rp.get("x_px", 0),
            y_px=rp.get("y_px", 0),
            height_ft=rp.get("height_ft", 4.0),
            notes=rp.get("notes", ""),
        ))

    metadata = {}
    if paths["floorplan_metadata"].exists():
        with open(paths["floorplan_metadata"], encoding="utf-8") as f:
            metadata = json.load(f)

    return config, rooms, routers, metadata
