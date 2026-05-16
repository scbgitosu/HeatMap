"""
Streamlit app: label rooms and place router positions on a floorplan.

Usage:
    streamlit run mac_analysis/floorplan_labeler.py -- --project survey_projects/apartment_test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import streamlit as st
from PIL import Image
from streamlit_drawable_canvas import st_canvas

from shared.utils import now_iso, project_paths


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="survey_projects/apartment_test")
    try:
        idx = sys.argv.index("--")
        args = parser.parse_args(sys.argv[idx + 1:])
    except ValueError:
        args = parser.parse_args([])
    return args


def _polygon_centroid(points: List[List[float]]) -> tuple:
    if not points:
        return 0.0, 0.0
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def main():
    args = _parse_args()
    project_dir = Path(args.project)
    paths = project_paths(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)

    st.set_page_config(page_title="Floorplan Labeler", layout="wide")
    st.title("Floorplan Labeler")
    st.caption(f"Project: `{project_dir}`")

    floorplan_path = paths["floorplan_png"]
    if not floorplan_path.exists():
        st.error(
            f"`{floorplan_path}` not found. Run **floorplan_import.py** first to generate a floorplan."
        )
        st.stop()

    img = Image.open(floorplan_path)
    img_w, img_h = img.size

    # Load metadata for scale info
    metadata = {}
    if paths["floorplan_metadata"].exists():
        with open(paths["floorplan_metadata"], encoding="utf-8") as f:
            metadata = json.load(f)

    # Load existing data
    existing_rooms = []
    if paths["rooms_json"].exists():
        with open(paths["rooms_json"], encoding="utf-8") as f:
            existing_rooms = json.load(f)

    existing_routers = []
    if paths["router_positions_json"].exists():
        with open(paths["router_positions_json"], encoding="utf-8") as f:
            existing_routers = json.load(f)

    tab_rooms, tab_routers, tab_config = st.tabs(["Rooms", "Router Positions", "Project Config"])

    # ---- Rooms tab ----
    with tab_rooms:
        st.subheader("Draw room polygons")
        st.info(
            "Select **Polygon** mode, click to add vertices, and double-click to close. "
            "Each completed polygon will appear in the table below — fill in room ID and name, then Save."
        )

        canvas_result = st_canvas(
            fill_color="rgba(100, 100, 200, 0.15)",
            stroke_width=2,
            stroke_color="#6464C8",
            background_image=img,
            update_streamlit=True,
            width=img_w,
            height=img_h,
            drawing_mode="polygon",
            key="rooms_canvas",
        )

        polygons = []
        if canvas_result.json_data and canvas_result.json_data.get("objects"):
            for obj in canvas_result.json_data["objects"]:
                if obj.get("type") == "path":
                    # streamlit-drawable-canvas returns polygon paths as SVG path segments
                    points = []
                    path = obj.get("path", [])
                    for seg in path:
                        if seg[0] in ("M", "L") and len(seg) >= 3:
                            points.append([seg[1], seg[2]])
                    if len(points) >= 3:
                        polygons.append(points)

        st.write(f"**{len(polygons)} polygon(s) drawn**")

        # Build editable room rows
        if polygons or existing_rooms:
            st.subheader("Room metadata")
            room_rows = []
            for i, poly in enumerate(polygons):
                cx, cy = _polygon_centroid(poly)
                # Pre-fill from existing if available
                existing = existing_rooms[i] if i < len(existing_rooms) else {}
                col1, col2 = st.columns(2)
                with col1:
                    rid = st.text_input(
                        f"Room ID #{i+1}",
                        value=existing.get("room_id", f"room_{i+1}"),
                        key=f"room_id_{i}",
                    )
                with col2:
                    rname = st.text_input(
                        f"Room name #{i+1}",
                        value=existing.get("room_name", ""),
                        key=f"room_name_{i}",
                    )
                room_rows.append({
                    "room_id": rid,
                    "room_name": rname,
                    "polygon": poly,
                    "label_x": cx,
                    "label_y": cy,
                })

            if st.button("Save rooms.json", type="primary"):
                with open(paths["rooms_json"], "w", encoding="utf-8") as f:
                    json.dump(room_rows, f, indent=2)
                st.success(f"Saved {len(room_rows)} rooms to `{paths['rooms_json']}`")
                st.json(room_rows)
        else:
            st.info("Draw at least one polygon above, then fill in names and save.")

        if existing_rooms:
            st.subheader("Currently saved rooms.json")
            st.json(existing_rooms)

    # ---- Router positions tab ----
    with tab_routers:
        st.subheader("Place router positions")
        st.info("Select **Point** mode, then click to place router/AP positions.")

        router_canvas = st_canvas(
            fill_color="rgba(255, 165, 0, 0.6)",
            stroke_width=2,
            stroke_color="#FFA500",
            background_image=img,
            update_streamlit=True,
            width=img_w,
            height=img_h,
            drawing_mode="point",
            point_display_radius=8,
            key="router_canvas",
        )

        router_points = []
        if router_canvas.json_data and router_canvas.json_data.get("objects"):
            for obj in router_canvas.json_data["objects"]:
                if obj.get("type") == "circle":
                    router_points.append([obj.get("left", 0), obj.get("top", 0)])

        st.write(f"**{len(router_points)} point(s) placed**")

        if router_points:
            st.subheader("Router position metadata")
            router_rows = []
            for i, (px, py) in enumerate(router_points):
                existing = existing_routers[i] if i < len(existing_routers) else {}
                col1, col2, col3 = st.columns(3)
                with col1:
                    rp_id = st.text_input(
                        f"Position ID #{i+1}",
                        value=existing.get("router_position_id", f"pos_{i+1}"),
                        key=f"rp_id_{i}",
                    )
                with col2:
                    rp_name = st.text_input(
                        f"Position name #{i+1}",
                        value=existing.get("name", ""),
                        key=f"rp_name_{i}",
                    )
                with col3:
                    rp_height = st.number_input(
                        f"Height ft #{i+1}",
                        0.0, 20.0,
                        value=float(existing.get("height_ft", 4.0)),
                        step=0.5,
                        key=f"rp_height_{i}",
                    )
                rp_notes = st.text_input(
                    f"Notes #{i+1}",
                    value=existing.get("notes", ""),
                    key=f"rp_notes_{i}",
                )
                router_rows.append({
                    "router_position_id": rp_id,
                    "name": rp_name,
                    "x_px": px,
                    "y_px": py,
                    "height_ft": rp_height,
                    "notes": rp_notes,
                })

            if st.button("Save router_positions.json", type="primary"):
                with open(paths["router_positions_json"], "w", encoding="utf-8") as f:
                    json.dump(router_rows, f, indent=2)
                st.success(f"Saved {len(router_rows)} positions to `{paths['router_positions_json']}`")

        if existing_routers:
            st.subheader("Currently saved router_positions.json")
            st.json(existing_routers)

    # ---- Config tab ----
    with tab_config:
        st.subheader("Project configuration")

        existing_cfg = {}
        if paths["project_config"].exists():
            with open(paths["project_config"], encoding="utf-8") as f:
                existing_cfg = json.load(f)

        col1, col2 = st.columns(2)
        with col1:
            proj_name = st.text_input(
                "Project name",
                value=existing_cfg.get("project_name", project_dir.name),
            )
            target_ssid = st.text_input(
                "Target SSID",
                value=existing_cfg.get("target_ssid", ""),
            )
            target_bssid = st.text_input(
                "Target BSSID (optional)",
                value=existing_cfg.get("target_bssid", ""),
            )
        with col2:
            default_iface = st.text_input(
                "Default Wi-Fi interface",
                value=existing_cfg.get("default_interface", "wlan0"),
            )
            units = st.selectbox(
                "Units",
                ["feet", "meters"],
                index=0 if existing_cfg.get("units", "feet") == "feet" else 1,
            )

        if st.button("Save project_config.json", type="primary"):
            cfg = {
                "project_name": proj_name,
                "target_ssid": target_ssid,
                "target_bssid": target_bssid,
                "default_interface": default_iface,
                "units": units,
                "collection_mode": "click_to_scan",
                "paths": {
                    "floorplan_png": str(paths["floorplan_png"]),
                    "rooms_json": str(paths["rooms_json"]),
                    "router_positions_json": str(paths["router_positions_json"]),
                    "survey_sessions_dir": str(paths["survey_sessions_dir"]),
                },
                "updated_at": now_iso(),
            }
            with open(paths["project_config"], "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
            st.success(f"Saved to `{paths['project_config']}`")
            st.json(cfg)

        if existing_cfg:
            st.subheader("Current project_config.json")
            st.json(existing_cfg)


if __name__ == "__main__":
    main()
