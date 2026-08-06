"""
Pulse - a live Bluetooth Low Energy radar.

WHAT THIS DOES
---------------
- Continuously scans for nearby BLE devices (name, MAC address, RSSI).
- Estimates distance from RSSI using the standard log-distance path-loss model.
- Plots devices on a polar ("radar") chart, color-coded by signal quality.
  Bluetooth gives NO real angle info, so each device is assigned a STABLE
  fake angle (derived from its MAC address) just so it doesn't jump around
  the screen between scans. Only the RADIUS (distance) is meaningful.
- Prints a live sorted table (closest first) in the terminal, with a signal
  strength bar per device.
- Optionally logs every detection to a CSV file for later analysis.

ACCURACY / LIMITATIONS (read before trusting the numbers)
-----------------------------------------------------------
RSSI-based distance is a rough estimate, not a measurement. Expect error of
1-3+ meters depending on environment. Sources of error:
  - Walls, furniture, your own body, device orientation
  - Different phones/devices transmit at different power levels
  - Multipath reflections indoors
  - The default --tx-power / --path-loss values below are generic guesses

TO IMPROVE ACCURACY:
  1. Calibrate --tx-power: place a known device exactly 1 meter away, read
     its RSSI from this script's output, and pass that value.
  2. Tune --path-loss for your environment:
       ~2.0 = free space / open outdoor area
       ~2.5-3.0 = typical indoor room, few obstacles (default: 2.5)
       ~3.5-4.0 = indoor with many walls/obstacles
  3. Average RSSI over several samples (this script already does a rolling
     average per device to reduce jitter -- see --smoothing).

INSTALL
-------
    pip install bleak matplotlib

RUN
---
    python ble_radar.py
    python ble_radar.py --tx-power -62 --path-loss 3.0 --range 15
    python ble_radar.py --csv session_log.csv

Requires a Bluetooth adapter on your machine, and (on Linux) usually needs
BlueZ + appropriate permissions (try running with sudo if scanning fails,
or add your user to the 'bluetooth' group).
"""

import argparse
import asyncio
import csv
import hashlib
import math
import sys
import threading
import time
from collections import deque
from datetime import datetime

import matplotlib.pyplot as plt
import matplotlib.animation as animation
from bleak import BleakScanner

# ---------------------------------------------------------------------------
# Signal-quality thresholds (used for color coding + the console bar)
# ---------------------------------------------------------------------------
QUALITY_BANDS = [
    # (min_rssi, label, color)
    (-60, "Strong", "#39FF14"),
    (-75, "Good", "#F5D742"),
    (-90, "Weak", "#FF6B35"),
    (-999, "Very Weak", "#FF3131"),
]


def quality_for(rssi):
    for min_rssi, label, color in QUALITY_BANDS:
        if rssi >= min_rssi:
            return label, color
    return QUALITY_BANDS[-1][1], QUALITY_BANDS[-1][2]


def signal_bar(rssi, width=10):
    """ASCII bar for the console table, scaled roughly -100..-30 dBm."""
    pct = max(0.0, min(1.0, (rssi + 100) / 70.0))
    filled = int(round(pct * width))
    return "█" * filled + "░" * (width - filled)


# ---------------------------------------------------------------------------
# Distance estimation
# ---------------------------------------------------------------------------
def estimate_distance(rssi, tx_power, n):
    """Log-distance path-loss model: RSSI = TxPower - 10*n*log10(d)."""
    if rssi == 0:
        return -1.0
    ratio = (tx_power - rssi) / (10.0 * n)
    return 10 ** ratio


def stable_angle_for(address: str) -> float:
    """Deterministic fake angle (radians) from a MAC address, purely for
    visual placement on the radar -- NOT a real bearing."""
    h = hashlib.md5(address.encode()).hexdigest()
    val = int(h[:8], 16)
    return (val % 3600) / 3600.0 * 2 * math.pi


# ---------------------------------------------------------------------------
# Shared state, updated by the scanner thread, read by the plotting thread
# ---------------------------------------------------------------------------
class RadarState:
    def __init__(self, smoothing, timeout):
        self.lock = threading.Lock()
        self.devices = {}
        self.smoothing = smoothing
        self.timeout = timeout

    def update(self, address, name, rssi):
        with self.lock:
            entry = self.devices.get(address)
            if entry is None:
                entry = {
                    "name": name or "Unknown",
                    "rssi_history": deque(maxlen=self.smoothing),
                    "angle": stable_angle_for(address),
                    "first_seen": time.time(),
                }
                self.devices[address] = entry
            if name and name != "Unknown":
                entry["name"] = name
            entry["rssi_history"].append(rssi)
            entry["last_seen"] = time.time()

    def prune_stale(self):
        now = time.time()
        with self.lock:
            stale = [a for a, d in self.devices.items()
                     if now - d.get("last_seen", 0) > self.timeout]
            for a in stale:
                del self.devices[a]

    def snapshot(self, tx_power, path_loss):
        with self.lock:
            out = []
            for addr, d in self.devices.items():
                avg_rssi = sum(d["rssi_history"]) / len(d["rssi_history"])
                dist = estimate_distance(avg_rssi, tx_power, path_loss)
                out.append({
                    "address": addr,
                    "name": d["name"],
                    "rssi": avg_rssi,
                    "distance": dist,
                    "angle": d["angle"],
                })
        return out


# ---------------------------------------------------------------------------
# CSV logging (optional, thread-safe)
# ---------------------------------------------------------------------------
class CsvLogger:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self._file = open(path, "a", newline="")
        self._writer = csv.writer(self._file)
        if self._file.tell() == 0:
            self._writer.writerow(["timestamp", "address", "name", "rssi_dbm", "est_distance_m"])

    def log(self, address, name, rssi, distance):
        with self.lock:
            self._writer.writerow([datetime.now().isoformat(timespec="seconds"),
                                    address, name, f"{rssi:.1f}", f"{distance:.2f}"])
            self._file.flush()

    def close(self):
        with self.lock:
            self._file.close()


# ---------------------------------------------------------------------------
# BLE scanning (runs in a background thread with its own asyncio loop)
# ---------------------------------------------------------------------------
def make_detection_callback(state: RadarState, csv_logger, tx_power, path_loss):
    def callback(device, advertisement_data):
        name = device.name or advertisement_data.local_name
        rssi = advertisement_data.rssi if advertisement_data.rssi is not None else device.rssi
        if rssi is None:
            return
        state.update(device.address, name, rssi)
        if csv_logger is not None:
            dist = estimate_distance(rssi, tx_power, path_loss)
            csv_logger.log(device.address, name or "Unknown", rssi, dist)
    return callback


async def scan_loop(state: RadarState, callback, interval, stop_event):
    scanner = BleakScanner(callback)
    await scanner.start()
    try:
        while not stop_event.is_set():
            await asyncio.sleep(interval)
            state.prune_stale()
    finally:
        await scanner.stop()


def run_scanner_thread(state, callback, interval, stop_event, error_box):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(scan_loop(state, callback, interval, stop_event))
    except Exception as e:
        error_box["error"] = e


# ---------------------------------------------------------------------------
# Live radar plot
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Pulse - a live Bluetooth LE radar.")
    parser.add_argument("--tx-power", type=float, default=-59,
                         help="Calibrated RSSI at 1 meter (dBm). Default: -59")
    parser.add_argument("--path-loss", type=float, default=2.5,
                         help="Path-loss exponent, 2.0 (open) to 4.0 (obstructed). Default: 2.5")
    parser.add_argument("--range", type=float, default=20,
                         help="Outer ring of the radar, in meters. Default: 20")
    parser.add_argument("--timeout", type=float, default=8,
                         help="Seconds before an unseen device drops off. Default: 8")
    parser.add_argument("--interval", type=float, default=1.0,
                         help="Seconds between scan/prune cycles. Default: 1.0")
    parser.add_argument("--smoothing", type=int, default=5,
                         help="Rolling-average window size for RSSI. Default: 5")
    parser.add_argument("--csv", type=str, default=None,
                         help="Optional path to append detection logs as CSV.")
    args = parser.parse_args()

    csv_logger = CsvLogger(args.csv) if args.csv else None
    state = RadarState(smoothing=args.smoothing, timeout=args.timeout)
    callback = make_detection_callback(state, csv_logger, args.tx_power, args.path_loss)
    stop_event = threading.Event()
    error_box = {}

    scanner_thread = threading.Thread(
        target=run_scanner_thread,
        args=(state, callback, args.interval, stop_event, error_box),
        daemon=True,
    )
    scanner_thread.start()

    print("Pulse: scanning for BLE devices... (close the plot window or Ctrl+C to stop)")
    if csv_logger:
        print(f"Logging detections to: {args.csv}")
    print(f"tx_power={args.tx_power} dBm  path_loss={args.path_loss}  range={args.range}m\n")

    fig = plt.figure(figsize=(7.5, 7.5))
    fig.patch.set_facecolor("#0b0f14")
    ax = fig.add_subplot(111, projection="polar")
    ax.set_facecolor("#0b0f14")
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_ylim(0, args.range)
    ax.tick_params(colors="#5b6b7a")
    ax.spines['polar'].set_color("#2a3540")
    ax.set_title("Pulse — BLE Radar  (angle is arbitrary, only distance is meaningful)",
                  color="#9fb3c2", pad=20, fontsize=10)

    scatter = ax.scatter([], [], s=90, edgecolors="black", zorder=3)
    sweep_line, = ax.plot([], [], color="#39FF14", alpha=0.4, linewidth=2, zorder=2)
    labels = []
    sweep_angle = [0.0]

    def update(frame):
        nonlocal labels

        if "error" in error_box:
            print(f"\n[!] Scanner error: {error_box['error']}")
            print("    Check that Bluetooth is enabled and permissions are granted.")

        for lbl in labels:
            lbl.remove()
        labels = []

        data = state.snapshot(args.tx_power, args.path_loss)
        data.sort(key=lambda d: d["distance"])

        if data:
            angles = [d["angle"] for d in data]
            radii = [min(d["distance"], args.range) for d in data]
            colors = [quality_for(d["rssi"])[1] for d in data]
            scatter.set_offsets(list(zip(angles, radii)))
            scatter.set_color(colors)
            for d in data:
                r = min(d["distance"], args.range)
                label = f"{d['name']}\n{d['distance']:.1f}m  {d['rssi']:.0f}dBm"
                txt = ax.annotate(
                    label, (d["angle"], r),
                    fontsize=7, color="#d7e3ea",
                    ha="center", va="bottom",
                    xytext=(0, 6), textcoords="offset points",
                )
                labels.append(txt)
        else:
            scatter.set_offsets([[0, 0]])

        sweep_angle[0] = (sweep_angle[0] + 0.15) % (2 * math.pi)
        sweep_line.set_data([sweep_angle[0], sweep_angle[0]], [0, args.range])

        # console table
        print("\033c", end="")
        print("PULSE — Live BLE Radar")
        print(f"{'Device':<22}{'RSSI':>7}  {'Signal':<12}{'Bar':<12}{'Dist (m)':>9}")
        print("-" * 70)
        for d in data:
            label, _ = quality_for(d["rssi"])
            bar = signal_bar(d["rssi"])
            name = d["name"][:21]
            print(f"{name:<22}{d['rssi']:>6.0f}   {label:<12}{bar:<12}{d['distance']:>9.2f}")
        if not data:
            print("(no devices detected yet)")
        if data:
            closest = data[0]
            print(f"\nClosest: {closest['name']} at ~{closest['distance']:.1f} m")

        return scatter, sweep_line

    ani = animation.FuncAnimation(fig, update, interval=1000, cache_frame_data=False)

    try:
        plt.show()
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        if csv_logger:
            csv_logger.close()
        print("\nPulse stopped.")


if __name__ == "__main__":
    main()