# Wi-Fi Apartment Survey

A practical site-survey tool for mapping Wi-Fi signal strength across an apartment. Walk the apartment, click on a floorplan view to capture RSSI at each position, then generate interpolated heatmaps on your Mac.

**v1 covers:** image-import floorplan preparation, room/router labeling, HP Ubuntu field collector, Mac heatmap generation.  
**Deferred to later:** GLB/3D conversion, session comparison, AP placement recommendations, automated reports.

---

## Mac setup

```bash
cd wifi-apartment-survey
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-mac.txt
```

## HP Ubuntu setup

```bash
cd wifi-apartment-survey
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-hp.txt
# nmcli and iw are system binaries — no pip install needed
sudo apt install network-manager iw   # if not already present
```

---

## Step-by-step workflow

### Step 1 — Import the floorplan (Mac)

Put a photo or blueprint image in `floorplans/raw/`, then run:

```bash
streamlit run mac_analysis/floorplan_import.py -- --project survey_projects/apartment_test
```

Crop, rotate, and optionally set scale (two-point distance). Click **Save** → generates:
- `survey_projects/apartment_test/floorplan.png`
- `survey_projects/apartment_test/floorplan_metadata.json`

### Step 2 — Label rooms and router positions (Mac)

```bash
streamlit run mac_analysis/floorplan_labeler.py -- --project survey_projects/apartment_test
```

- **Rooms tab:** draw polygon outlines around each room, fill in IDs and names, click Save.
- **Router positions tab:** click to place router/AP candidate dots, fill in IDs and names, click Save.
- **Project Config tab:** set target SSID, BSSID (optional), default Wi-Fi interface, click Save.

Generates: `rooms.json`, `router_positions.json`, `project_config.json`.

### Step 3 — Transfer the project to the HP

```bash
# From Mac:
rsync -av survey_projects/apartment_test/ user@hp-laptop:~/wifi-survey/survey_projects/apartment_test/
```

Or copy the `survey_projects/apartment_test/` folder via USB.

### Step 4 — Sanity-check Wi-Fi scanning (HP)

```bash
python3 hp_collector/wifi_scan.py --interface wlan1 --ssid "YourNetworkName" --samples 5
```

Confirms that nmcli/iw can see your network and returns reasonable dBm values.

### Step 5 — Run the field collector (HP)

```bash
python3 hp_collector/collector_app.py --project survey_projects/apartment_test
```

- Select router position and session name in the left panel.
- Left-click on the floorplan where you're standing.
- Wait for the status banner to show **Saved** (the dot turns green/yellow/red based on RSSI).
- Walk to the next spot, repeat. Use **Undo** if you misclick.
- Close the app when done — all data is flushed to disk after every click.

### Step 6 — Transfer measurements back to Mac

```bash
# From Mac:
rsync -av user@hp-laptop:~/wifi-survey/survey_projects/apartment_test/survey_sessions/ \
    survey_projects/apartment_test/survey_sessions/
```

### Step 7 — Generate heatmaps (Mac)

```bash
python3 mac_analysis/heatmap_generator.py \
    --project survey_projects/apartment_test \
    --session baseline_current_router \
    --output-dir output/heatmaps
```

Produces three images in `output/heatmaps/`:
- `baseline_current_router_points.png` — colored scatter plot of measurement points
- `baseline_current_router_heatmap.png` — interpolated RSSI heatmap with colorbar
- `baseline_current_router_weak_zones.png` — highlights areas below −70 dBm

---

## Data layout

```
survey_projects/apartment_test/
  project_config.json          # SSID, interface, paths
  floorplan.png                # prepared floorplan image
  floorplan_metadata.json      # size, scale, source info
  rooms.json                   # room polygons and labels
  router_positions.json        # AP candidate positions
  survey_sessions/
    baseline_current_router/
      measurements_raw.csv     # one row per BSSID per scan
      measurements_summary.csv # one row per click point
```

---

## Troubleshooting

**No Wi-Fi interfaces listed in the collector app:**
```bash
nmcli device         # list all network devices
iw dev               # alternative
```
The interface is usually `wlan0` or `wlan1`. Edit the interface field in the app sidebar.

**nmcli requires sudo / returns error:**  
Try running with `--backend iw` in `wifi_scan.py` CLI. Note that `iw` scans require `sudo`.

**`floorplan.png` missing error in collector app:**  
Run `floorplan_import.py` on the Mac first and transfer the project folder to the HP.

**`rooms.json` missing error:**  
Run `floorplan_labeler.py` on the Mac, draw at least one room polygon, and save.

**Heatmap looks blurry / wrong shape:**  
Increase measurement density — aim for at least 15–20 clicks spread across the space. The interpolation quality degrades with sparse data near room edges.

---

## Limitations (v1)

- RSSI measurements are highly variable; 10 samples per point reduces noise but does not eliminate it. Repeat surveys improve reliability.
- The nmcli SIGNAL→dBm conversion (`signal/2 - 100`) is an approximation. Exact dBm is only available via `iw` (which requires root).
- Room polygon masking only applies if polygons are defined in `rooms.json`. Without polygons the heatmap interpolates across the entire image.
- `iw` fallback scans are slower and require `sudo`; prefer `nmcli` on the HP.
- 3D RF modeling, session comparison, and AP placement recommendations are deferred to a later version.
