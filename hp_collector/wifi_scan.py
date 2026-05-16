"""
Wi-Fi scanning backend: nmcli (primary), iw (fallback).

CLI usage:
    python3 hp_collector/wifi_scan.py --interface wlan1 --ssid "MySSID" --samples 10
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
import logging
from typing import List, Optional

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from shared.models import Sample
from shared.utils import now_iso

logger = logging.getLogger(__name__)


def list_wifi_interfaces() -> List[str]:
    """Return Wi-Fi interface names visible to nmcli or iw."""
    try:
        out = subprocess.check_output(
            ["nmcli", "-t", "-f", "DEVICE,TYPE", "device"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        ifaces = []
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1].strip() == "wifi":
                ifaces.append(parts[0].strip())
        if ifaces:
            return ifaces
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    try:
        out = subprocess.check_output(["iw", "dev"], text=True, stderr=subprocess.DEVNULL)
        ifaces = re.findall(r"Interface\s+(\S+)", out)
        if ifaces:
            return ifaces
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    return []


def _signal_to_dbm(signal: int) -> float:
    """Convert nmcli 0-100 signal quality to approximate dBm."""
    return (signal / 2) - 100


def scan_nmcli(
    interface: str,
    ssid: Optional[str] = None,
    bssid: Optional[str] = None,
) -> List[dict]:
    """
    Run nmcli Wi-Fi scan. Returns list of raw AP dicts.
    Fields: ssid, bssid, rssi_dbm, channel, frequency_mhz.
    """
    cmd = [
        "nmcli", "-t",
        "-f", "SSID,BSSID,SIGNAL,CHAN,FREQ,SECURITY",
        "device", "wifi", "list",
        "ifname", interface,
        "--rescan", "yes",
    ]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE, timeout=15)
    except FileNotFoundError:
        raise RuntimeError("nmcli not found")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"nmcli error: {e.stderr.strip()}")

    results = []
    for line in out.splitlines():
        if not line.strip():
            continue
        # nmcli -t escapes colons inside field values as \:
        # Split on unescaped colons using a regex
        parts = re.split(r"(?<!\\):", line)
        parts = [p.replace(r"\:", ":") for p in parts]
        if len(parts) < 5:
            continue
        ap_ssid, ap_bssid, signal_str, chan_str, freq_str = parts[:5]
        if ssid and ap_ssid != ssid:
            continue
        if bssid and ap_bssid.upper() != bssid.upper():
            continue
        try:
            sig = int(signal_str)
            rssi = _signal_to_dbm(sig)
        except ValueError:
            rssi = None
        try:
            chan = int(chan_str)
        except ValueError:
            chan = None
        freq = None
        m = re.search(r"(\d+)", freq_str.replace(" ", ""))
        if m:
            freq = float(m.group(1))
        results.append({
            "ssid": ap_ssid,
            "bssid": ap_bssid,
            "rssi_dbm": rssi,
            "channel": chan,
            "frequency_mhz": freq,
        })
    return results


def scan_iw(
    interface: str,
    ssid: Optional[str] = None,
    bssid: Optional[str] = None,
) -> List[dict]:
    """
    Run iw Wi-Fi scan (may need sudo). Returns list of raw AP dicts.
    """
    logger.warning(
        "Falling back to iw scan — this may require sudo and takes longer."
    )
    cmd = ["sudo", "iw", "dev", interface, "scan"]
    try:
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE, timeout=30)
    except FileNotFoundError:
        raise RuntimeError("iw not found")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"iw error: {e.stderr.strip()}")

    results = []
    current: dict = {}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith("BSS "):
            if current:
                _maybe_append(current, ssid, bssid, results)
            bss_bssid = line.split()[1].split("(")[0].strip()
            current = {"bssid": bss_bssid, "ssid": "", "rssi_dbm": None,
                       "channel": None, "frequency_mhz": None}
        elif line.startswith("signal:"):
            m = re.search(r"(-?\d+\.?\d*)", line)
            if m:
                current["rssi_dbm"] = float(m.group(1))
        elif line.startswith("freq:"):
            m = re.search(r"(\d+)", line)
            if m:
                current["frequency_mhz"] = float(m.group(1))
        elif line.startswith("* primary channel:") or line.startswith("DS Parameter set: channel"):
            m = re.search(r"(\d+)", line)
            if m:
                current["channel"] = int(m.group(1))
        elif line.startswith("SSID:"):
            current["ssid"] = line[5:].strip()
    if current:
        _maybe_append(current, ssid, bssid, results)
    return results


def _maybe_append(ap: dict, ssid_filter, bssid_filter, results: list):
    if ssid_filter and ap.get("ssid") != ssid_filter:
        return
    if bssid_filter and ap.get("bssid", "").upper() != bssid_filter.upper():
        return
    results.append(ap)


def scan(
    interface: str,
    ssid: Optional[str] = None,
    bssid: Optional[str] = None,
    backend: str = "auto",
) -> List[dict]:
    """Scan using nmcli (or iw fallback). Returns list of AP dicts."""
    if backend in ("nmcli", "auto"):
        try:
            return scan_nmcli(interface, ssid, bssid), "nmcli"
        except RuntimeError as e:
            if backend == "nmcli":
                raise
            logger.warning(f"nmcli failed ({e}); trying iw")
    return scan_iw(interface, ssid, bssid), "iw"


def collect_samples(
    interface: str,
    ssid: str,
    bssid: Optional[str] = None,
    samples: int = 10,
    delay_s: float = 0.5,
    click_context: Optional[dict] = None,
) -> List[Sample]:
    """
    Run `samples` scans and return all Sample records (one per BSSID per scan).
    click_context: dict with click_id, session_id, router_position_id, x_px, y_px,
                   room_id, room_name, height_ft.
    """
    if click_context is None:
        click_context = {
            "click_id": "cli_test",
            "session_id": "cli_test",
            "router_position_id": "cli_test",
            "x_px": 0,
            "y_px": 0,
            "room_id": "unknown",
            "room_name": "unknown",
            "height_ft": 0,
        }

    all_samples = []
    for i in range(1, samples + 1):
        ts = now_iso()
        try:
            aps, backend_used = scan(interface, ssid, bssid)
        except RuntimeError as e:
            logger.error(f"Scan {i} failed: {e}")
            all_samples.append(Sample(
                timestamp=ts,
                session_id=click_context["session_id"],
                router_position_id=click_context["router_position_id"],
                click_id=click_context["click_id"],
                x_px=click_context["x_px"],
                y_px=click_context["y_px"],
                room_id=click_context["room_id"],
                room_name=click_context["room_name"],
                height_ft=click_context["height_ft"],
                ssid=ssid,
                bssid=bssid or "",
                frequency_mhz=None,
                channel=None,
                rssi_dbm=None,
                interface=interface,
                scan_backend="error",
                sample_number=i,
                note=str(e),
            ))
            if i < samples:
                time.sleep(delay_s)
            continue

        if not aps:
            all_samples.append(Sample(
                timestamp=ts,
                session_id=click_context["session_id"],
                router_position_id=click_context["router_position_id"],
                click_id=click_context["click_id"],
                x_px=click_context["x_px"],
                y_px=click_context["y_px"],
                room_id=click_context["room_id"],
                room_name=click_context["room_name"],
                height_ft=click_context["height_ft"],
                ssid=ssid,
                bssid=bssid or "",
                frequency_mhz=None,
                channel=None,
                rssi_dbm=None,
                interface=interface,
                scan_backend=backend_used,
                sample_number=i,
                note="network_not_found",
            ))
        else:
            for ap in aps:
                all_samples.append(Sample(
                    timestamp=ts,
                    session_id=click_context["session_id"],
                    router_position_id=click_context["router_position_id"],
                    click_id=click_context["click_id"],
                    x_px=click_context["x_px"],
                    y_px=click_context["y_px"],
                    room_id=click_context["room_id"],
                    room_name=click_context["room_name"],
                    height_ft=click_context["height_ft"],
                    ssid=ap.get("ssid", ssid),
                    bssid=ap.get("bssid", ""),
                    frequency_mhz=ap.get("frequency_mhz"),
                    channel=ap.get("channel"),
                    rssi_dbm=ap.get("rssi_dbm"),
                    interface=interface,
                    scan_backend=backend_used,
                    sample_number=i,
                    note="",
                ))

        if i < samples:
            time.sleep(delay_s)

    return all_samples


def summarize(
    samples: List[Sample],
    target_ssid: str,
    target_bssid: Optional[str] = None,
) -> dict:
    """
    Summarize a list of Sample records for one click point.
    Returns a dict matching SUMMARY_COLUMNS.
    """
    import statistics

    filtered = [s for s in samples if s.ssid == target_ssid]
    if target_bssid:
        filtered = [s for s in filtered if s.bssid.upper() == target_bssid.upper()]

    valid = [s for s in filtered if s.rssi_dbm is not None]
    missing = len([s for s in samples if s.rssi_dbm is None])

    if not valid:
        rssi_avg = rssi_min = rssi_max = rssi_std = None
        best_bssid = target_bssid or ""
        freq = channel = None
    else:
        # Group by bssid to find strongest average
        bssid_groups: dict[str, list] = {}
        for s in valid:
            bssid_groups.setdefault(s.bssid, []).append(s.rssi_dbm)
        best_bssid = max(bssid_groups, key=lambda b: sum(bssid_groups[b]) / len(bssid_groups[b]))

        rssi_values = [s.rssi_dbm for s in valid]
        rssi_avg = sum(rssi_values) / len(rssi_values)
        rssi_min = min(rssi_values)
        rssi_max = max(rssi_values)
        rssi_std = statistics.stdev(rssi_values) if len(rssi_values) > 1 else 0.0

        # Use metadata from the last valid sample
        last = valid[-1]
        freq = last.frequency_mhz
        channel = last.channel

    ctx = samples[0] if samples else None
    return {
        "timestamp_start": samples[0].timestamp if samples else now_iso(),
        "timestamp_end": samples[-1].timestamp if samples else now_iso(),
        "session_id": ctx.session_id if ctx else "",
        "router_position_id": ctx.router_position_id if ctx else "",
        "click_id": ctx.click_id if ctx else "",
        "x_px": ctx.x_px if ctx else 0,
        "y_px": ctx.y_px if ctx else 0,
        "room_id": ctx.room_id if ctx else "",
        "room_name": ctx.room_name if ctx else "",
        "height_ft": ctx.height_ft if ctx else 0,
        "target_ssid": target_ssid,
        "target_bssid": target_bssid or "",
        "best_bssid": best_bssid,
        "frequency_mhz": freq,
        "channel": channel,
        "sample_count": len(valid),
        "rssi_avg_dbm": round(rssi_avg, 2) if rssi_avg is not None else None,
        "rssi_min_dbm": round(rssi_min, 2) if rssi_min is not None else None,
        "rssi_max_dbm": round(rssi_max, 2) if rssi_max is not None else None,
        "rssi_std_db": round(rssi_std, 3) if rssi_std is not None else None,
        "missing_sample_count": missing,
        "note": "",
    }


def _pretty_print(samples: List[Sample], summary: dict):
    print("\n=== Raw Samples ===")
    print(f"{'#':<4} {'BSSID':<20} {'SSID':<25} {'RSSI':>8}  {'Chan':>4}  {'Freq':>8}")
    print("-" * 75)
    for s in samples:
        rssi = f"{s.rssi_dbm:.1f}" if s.rssi_dbm is not None else "N/A"
        chan = str(s.channel) if s.channel else "?"
        freq = f"{s.frequency_mhz:.0f}" if s.frequency_mhz else "?"
        print(f"{s.sample_number:<4} {s.bssid:<20} {s.ssid:<25} {rssi:>8}  {chan:>4}  {freq:>8}")

    print("\n=== Summary ===")
    for k, v in summary.items():
        print(f"  {k:<25}: {v}")


def main():
    parser = argparse.ArgumentParser(description="Wi-Fi scan CLI")
    parser.add_argument("--interface", required=True, help="Wi-Fi interface (e.g. wlan1)")
    parser.add_argument("--ssid", required=True, help="Target SSID")
    parser.add_argument("--bssid", default=None, help="Optional target BSSID filter")
    parser.add_argument("--samples", type=int, default=10, help="Number of scans")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between scans (s)")
    parser.add_argument("--backend", choices=["auto", "nmcli", "iw"], default="auto")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    ifaces = list_wifi_interfaces()
    print(f"Detected Wi-Fi interfaces: {ifaces}")

    print(f"\nCollecting {args.samples} samples from {args.interface} for SSID '{args.ssid}'...")
    samples = collect_samples(
        interface=args.interface,
        ssid=args.ssid,
        bssid=args.bssid,
        samples=args.samples,
        delay_s=args.delay,
    )
    summary = summarize(samples, args.ssid, args.bssid)
    _pretty_print(samples, summary)


if __name__ == "__main__":
    main()
