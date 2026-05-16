# Wi-Fi Apartment Survey

A practical site-survey tool for mapping Wi-Fi signal strength across an apartment. Walk the apartment, click on a floorplan view to capture RSSI at each position, then generate interpolated heatmaps on your Mac.

**v1 covers:** image-import floorplan preparation, room/router/walk labeling, HP Ubuntu field collector, Mac heatmap generation, session comparison, and path-loss placement suggestions.  
**Deferred to later:** GLB/3D conversion, material-aware RF modeling, automated reports.

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
# Wi-Fi tools (nmcli, iw) and Qt platform plugin libs are system packages.
sudo apt install network-manager iw \
    qtwayland5 libxcb-cursor0 libxkbcommon-x11-0 libxcb-xinerama0
```

The `qtwayland5` / `libxcb-*` packages are what PyQt5 needs to attach to your
Ubuntu session (Wayland *or* Xorg). Without them you'll see
`Could not load the Qt platform plugin` / `Aborted (core dumped)` at launch.

---

## Step-by-step workflow

### Step 1 — Import the floorplan (Mac)

Put a photo or blueprint image in `floorplans/raw/`, then run:

```bash
streamlit run mac_analysis/floorplan_import.py -- --project survey_projects/apartment_test
```

Crop, rotate, and measure one known wall to set `scale_pixels_per_foot`. The scale is optional for basic heatmaps but required for path-loss placement optimization. Click **Save** → generates:
- `survey_projects/apartment_test/floorplan.png`
- `survey_projects/apartment_test/floorplan_metadata.json`

### Step 2 — Label rooms and router positions (Mac)

```bash
streamlit run mac_analysis/floorplan_labeler.py -- --project survey_projects/apartment_test
```

- **Rooms tab:** draw polygon outlines around each room, fill in IDs and names, click Save.
- **Router Positions tab:** click to place router/AP candidate dots, fill in IDs and names, click Save.
- **Walk Template tab:** click ordered survey waypoints so each router trial can use the same walk path.
- **Project Config tab:** set target SSID, BSSID (optional), default Wi-Fi interface, scan backend, click Save.

Generates: `rooms.json`, `router_positions.json`, `walk_waypoints.json`, `project_config.json`.

### Step 3 — Transfer the project to the HP

```bash
# From Mac:
rsync -av survey_projects/apartment_test/ user@hp-laptop:~/wifi-survey/survey_projects/apartment_test/
```

Or copy the `survey_projects/apartment_test/` folder via USB.

### Step 4 — Sanity-check Wi-Fi scanning (HP)

Run the preflight check (uses `project_config.json` for interface, SSID, and scan backend):

```bash
python3 hp_collector/preflight.py --project survey_projects/apartment_test
```

Or override settings manually:

```bash
python3 hp_collector/preflight.py \
  --project survey_projects/apartment_test \
  --interface wlan1 \
  --ssid "YourNetworkName"
```

Preflight verifies the adapter exists, the link is UP, rfkill is not blocking, and a trial scan finds your target SSID. Exit code 0 means you are ready to survey.

The default backend is `iw`, which reads real RSSI in dBm. When the laptop is connected to the target AP, collection also records `iw link` stats such as SNR, TX/RX bitrate, and MCS. Passive scans still collect neighbor BSS rows for co-channel interference scoring.

For deeper debugging, collect sample RSSI readings:

```bash
python3 hp_collector/wifi_scan.py --interface wlan1 --ssid "YourNetworkName" --samples 5
```

### Step 5 — Run the field collector (HP)

```bash
./scripts/run_collector.sh --project survey_projects/apartment_test
```

The wrapper runs Wi-Fi preflight first (same checks as Step 4), then auto-detects
whether your session is Wayland or Xorg, sets `QT_QPA_PLATFORM` accordingly,
falls back to the other plugin once on failure, and prints the exact `apt install`
line if any Qt runtime libs are missing.

The collector blocks floorplan clicks until preflight passes. Use **Re-check Wi-Fi**
in the sidebar after fixing hardware issues.

Running `python3 hp_collector/collector_app.py --project ...` directly also
works — the app performs the same auto-detection — but the wrapper gives you
the venv activation and apt-package probe for free.

- Select router position and session name in the left panel.
- Leave scan backend on `iw` unless you need `auto`/`nmcli` fallback.
- If you created a walk template, keep **Guided walk waypoint snap** enabled and click near the next waypoint.
- Left-click on the floorplan where you're standing.
- Wait for the status banner to show **Saved** (or **Partial scan** if some samples failed).
  Failed clicks are not saved — the dot is removed and the banner shows the error.
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

### Step 8 — Compare router placement trials (Mac)

After surveying the apartment once per router candidate (three sessions, three walks):

```bash
python3 mac_analysis/session_compare.py \
    --project survey_projects/apartment_test \
    --sessions trial_pos1,trial_pos2,trial_pos3 \
    --output-dir output/comparison \
    --export-heatmaps
```

Omit `--sessions` to compare every session under `survey_sessions/`. Outputs:

- `comparison_metrics.csv` — point stats, stability (RSSI std), SNR/link rate, neighbor interference proxy, interpolated area coverage, composite score
- `comparison_by_room.csv` — per-room breakdown per trial
- `walk_pairs.csv` — matched point pairs across sessions when a walk template or nearest-neighbor matching is available
- `comparison_ranking.png` — recommended router position ranking
- `comparison_bars.png` — coverage, worst-case, and stability side-by-side
- `comparison_matched_walk_deltas.png` — paired RSSI deltas at the same walk locations
- `comparison_best_vs_worst.png` — interpolated RSSI difference (best trial minus worst)
- `heatmaps/` — per-session heatmaps when `--export-heatmaps` is set

Use the same walk pattern (similar click positions) across trials so comparisons reflect router location, not different paths.

### Step 9 — Suggest new AP coordinates (Mac)

After at least three router-position trials and a calibrated floorplan scale:

```bash
python3 mac_analysis/placement_optimizer.py \
    --project survey_projects/apartment_test \
    --sessions trial_pos1,trial_pos2,trial_pos3 \
    --output-dir output/placement
```

Outputs:

- `model_params.json` — fitted path-loss exponent, cross-room penalty, RMSE
- `placement_recommendation.json` — ranked suggested coordinates beyond labeled router positions
- `predicted_coverage_rank1.png` — predicted heatmap for the top suggested AP location

---

## Data layout

```
survey_projects/apartment_test/
  project_config.json          # SSID, interface, paths
  floorplan.png                # prepared floorplan image
  floorplan_metadata.json      # size, scale, source info
  rooms.json                   # room polygons and labels
  router_positions.json        # AP candidate positions
  walk_waypoints.json          # optional matched-walk survey points
  survey_sessions/
    baseline_current_router/
      measurements_raw.csv     # one row per BSSID per scan
      measurements_summary.csv # one row per click point
```

---

## Troubleshooting

**Collector exits with `Could not load the Qt platform plugin` / `Failed to create wl_display` / `Aborted (core dumped)`:**

The Qt platform plugin can't attach to your display server. Two causes:

1. *Wrong plugin for the session.* Wayland sessions need `QT_QPA_PLATFORM=wayland`; Xorg sessions need `xcb`. The wrapper script picks the right one automatically:

   ```bash
   ./scripts/run_collector.sh --project survey_projects/apartment_test
   ```

2. *Missing native libs.* Install the full Qt platform support set:

   ```bash
   sudo apt install qtwayland5 libxcb-cursor0 libxkbcommon-x11-0 libxcb-xinerama0
   ```

For a verbose diagnostic, prefix the command with `QT_DEBUG_PLUGINS=1`. If you're on SSH, you must use `ssh -X` (and have `xauth` installed) to forward the display.

**No Wi-Fi interfaces listed in the collector app:**
```bash
nmcli device         # list all network devices
iw dev               # alternative
```
The interface is usually `wlan0` or `wlan1`. Edit the interface field in the app sidebar.

**`iw error: Network is down (-100)` or preflight reports interface DOWN:**

The USB Wi-Fi adapter exists in config but the kernel link is down. On the HP:

```bash
ip link show <your-interface>    # e.g. wlxc01c304311fe
rfkill list
sudo rfkill unblock wifi
sudo ip link set <your-interface> up
python3 hp_collector/preflight.py --project survey_projects/apartment_test
```

Common causes: adapter unplugged after sleep, rfkill soft-block, or wrong interface name after reboot.

**`iw` asks for a sudo password or preflight cannot scan unattended:**  
`iw scan` often requires root. For field collection, configure passwordless sudo for the exact `iw` binary on the HP:

```bash
which iw
sudo visudo
```

Add a narrow rule for your user and adapter host, using the path from `which iw`:

```text
your_linux_user ALL=(root) NOPASSWD: /usr/sbin/iw
```

Then re-run:

```bash
python3 hp_collector/preflight.py --project survey_projects/apartment_test --backend iw
```

**Need nmcli fallback:**  
Use `--backend auto` or select `auto` in the collector sidebar.

**`floorplan.png` missing error in collector app:**  
Run `floorplan_import.py` on the Mac first and transfer the project folder to the HP.

**`rooms.json` missing error:**  
Run `floorplan_labeler.py` on the Mac, draw at least one room polygon, and save.

**Heatmap looks blurry / wrong shape:**  
Increase measurement density — aim for at least 15–20 clicks spread across the space. The interpolation quality degrades with sparse data near room edges.

---

## Limitations (v1)

- RSSI measurements are highly variable; 10 samples per point reduces noise but does not eliminate it. Repeat surveys improve reliability.
- `iw link` SNR, bitrate, and MCS only exist when the survey laptop is associated with the target AP. Passive scans still work without link stats.
- Room polygon masking only applies if polygons are defined in `rooms.json`. Without polygons the heatmap interpolates across the entire image.
- The placement optimizer is a fitted path-loss heuristic, not full material-aware ray tracing. Treat suggested coordinates as high-quality candidates to validate with another walk.
- 3D RF modeling and per-wall material attenuation are deferred to a later version.
