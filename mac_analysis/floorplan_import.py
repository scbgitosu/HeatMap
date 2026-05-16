"""
Streamlit app: import and prepare a floorplan image.

Usage:
    streamlit run mac_analysis/floorplan_import.py -- --project survey_projects/apartment_test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st
from PIL import Image

from shared.utils import now_iso, project_paths

# --- Argument parsing (Streamlit passes args after --) ---
def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="survey_projects/apartment_test")
    try:
        idx = sys.argv.index("--")
        args = parser.parse_args(sys.argv[idx + 1:])
    except ValueError:
        args = parser.parse_args([])
    return args


def main():
    args = _parse_args()
    project_dir = Path(args.project)
    paths = project_paths(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    paths["floorplan_metadata"].parent.mkdir(parents=True, exist_ok=True)

    st.set_page_config(page_title="Floorplan Import", layout="wide")
    st.title("Floorplan Import")
    st.caption(f"Project: `{project_dir}`")

    raw_dir = Path("floorplans/raw")
    raw_dir.mkdir(parents=True, exist_ok=True)

    # --- File selection ---
    raw_images = sorted(raw_dir.glob("*"))
    img_options = [f.name for f in raw_images if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}]

    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("1. Select source image")
        if img_options:
            selected = st.selectbox("Image in floorplans/raw/", img_options)
            src_path = raw_dir / selected
        else:
            st.info("No images found in `floorplans/raw/`. Upload one below.")
            uploaded = st.file_uploader("Upload floorplan image", type=["png", "jpg", "jpeg", "bmp"])
            if uploaded:
                src_path = raw_dir / uploaded.name
                with open(src_path, "wb") as f:
                    f.write(uploaded.read())
                st.success(f"Saved to {src_path}")
                st.rerun()
            else:
                st.stop()

        img = Image.open(src_path)
        w0, h0 = img.size
        st.write(f"Original size: {w0} × {h0} px")

        st.subheader("2. Rotation")
        rotation = st.selectbox("Rotate", [0, 90, 180, 270], format_func=lambda x: f"{x}°")

        st.subheader("3. Crop (pixels from original)")
        left = st.number_input("Left", 0, w0 - 1, 0, step=1)
        top = st.number_input("Top", 0, h0 - 1, 0, step=1)
        right = st.number_input("Right", 1, w0, w0, step=1)
        bottom = st.number_input("Bottom", 1, h0, h0, step=1)

        st.subheader("4. Scale (optional)")
        known_dist_ft = st.number_input("Known real distance (feet)", 0.0, 1000.0, 0.0, step=0.5)
        known_dist_px = st.number_input("That distance in pixels (on cropped image)", 0.0, 10000.0, 0.0, step=1.0)

        notes = st.text_area("Notes (optional)")

    with col2:
        st.subheader("Preview")
        # Apply operations for preview
        preview = img.copy()
        if rotation:
            preview = preview.rotate(-rotation, expand=True)
        if left < right and top < bottom:
            preview = preview.crop((left, top, right, bottom))
        st.image(preview, caption="Processed preview", use_column_width=True)

        new_w, new_h = preview.size
        st.write(f"Output size: {new_w} × {new_h} px")

        scale_ppf = None
        if known_dist_ft > 0 and known_dist_px > 0:
            scale_ppf = known_dist_px / known_dist_ft
            st.write(f"Scale: **{scale_ppf:.2f} px/ft**")

        st.subheader("5. Save")
        if st.button("Save floorplan.png + metadata", type="primary"):
            out_dir = Path("floorplans/processed")
            out_dir.mkdir(parents=True, exist_ok=True)

            processed = img.copy()
            if rotation:
                processed = processed.rotate(-rotation, expand=True)
            if left < right and top < bottom:
                processed = processed.crop((left, top, right, bottom))

            out_path = paths["floorplan_png"]
            processed.save(str(out_path))

            metadata = {
                "image_width_px": new_w,
                "image_height_px": new_h,
                "units": "feet",
                "scale_pixels_per_foot": scale_ppf,
                "origin": {"x": 0, "y": 0},
                "source": str(src_path),
                "rotation_applied": rotation,
                "crop_applied": [left, top, right, bottom] if (left > 0 or top > 0 or right < w0 or bottom < h0) else None,
                "notes": notes,
                "created_at": now_iso(),
            }
            with open(paths["floorplan_metadata"], "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2)

            st.success(f"Saved to `{out_path}` and `{paths['floorplan_metadata']}`")
            st.json(metadata)


if __name__ == "__main__":
    main()
