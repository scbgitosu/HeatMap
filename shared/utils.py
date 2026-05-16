from __future__ import annotations

import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from shared.models import RoomLabel


def point_in_polygon(x: float, y: float, polygon: List[Tuple[float, float]]) -> bool:
    """Ray-cast algorithm. Returns True if (x, y) is inside polygon."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    px, py = polygon[-1]
    for qx, qy in polygon:
        if ((qy > y) != (py > y)) and (x < (px - qx) * (y - qy) / (py - qy) + qx):
            inside = not inside
        px, py = qx, qy
    return inside


def infer_room(x: float, y: float, rooms: List[RoomLabel]) -> Optional[RoomLabel]:
    """Returns first RoomLabel whose polygon contains (x, y), or None."""
    for room in rooms:
        if room.polygon and point_in_polygon(x, y, room.polygon):
            return room
    return None


def generate_click_id(session_id: str, n: int) -> str:
    """Generate a click ID like baseline_current_router_0001."""
    return f"{session_id}_{n:04d}"


def now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def project_paths(project_dir: Path) -> dict:
    """Resolve all standard subpaths from a project root directory."""
    d = Path(project_dir)
    return {
        "project_dir": d,
        "floorplan_png": d / "floorplan.png",
        "floorplan_metadata": d / "floorplan_metadata.json",
        "project_config": d / "project_config.json",
        "rooms_json": d / "rooms.json",
        "router_positions_json": d / "router_positions.json",
        "walk_waypoints_json": d / "walk_waypoints.json",
        "survey_sessions_dir": d / "survey_sessions",
    }


def px_to_ft(distance_px: float, metadata: dict) -> float:
    """Convert image-pixel distance to feet using floorplan metadata scale."""
    scale = metadata.get("scale_pixels_per_foot")
    if not scale:
        raise ValueError(
            "Missing floorplan scale. Re-run floorplan_import.py and measure a known wall length."
        )
    return distance_px / float(scale)
