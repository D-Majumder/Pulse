<h1 align="center">Pulse: A Live Bluetooth Radar</h1>

<p align="center">
  <i>"No angle, no lies — just how far, how strong, right now."</i>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white" alt="Python Badge">
  <img src="https://img.shields.io/badge/Bleak-BLE_Scanning-0078D4?logo=bluetooth&logoColor=white" alt="Bleak Badge">
  <img src="https://img.shields.io/badge/Matplotlib-Live_Plot-11557C?logo=plotly&logoColor=white" alt="Matplotlib Badge">
  <img src="https://img.shields.io/badge/Storage-Local_CSV_(optional)-4CAF50" alt="Storage Badge">
  <img src="https://img.shields.io/badge/Backend-None-lightgrey" alt="No Backend Badge">
  <img src="https://img.shields.io/badge/Works-Cross--Platform-9C27B0" alt="Cross Platform Badge">
</p>

---

## Overview

**Pulse** is a single-script Bluetooth Low Energy radar for people who want to *see* what's broadcasting around them without installing a bulky app or handing data to a cloud service.

There's no backend, no account, and nothing leaves your machine unless you turn on optional CSV logging — and even then, it just writes to a local file. Pulse listens for nearby BLE advertisements, estimates how far away each device is from its signal strength, and draws them live on a radar-style polar plot.

*A signal log, not a tracking product. Every device you see is only ever visible to you, on your own machine.*

**Honesty note:** Bluetooth doesn't transmit direction. The "angle" on the radar is a stable, fake position derived from each device's MAC address — it exists so dots don't jump around between scans. The **distance** is a real estimate (from RSSI via the log-distance path-loss model), but it's inherently approximate — expect ±1–3 m depending on your environment. See [Calibration](#calibration-for-better-accuracy) below.

---

## Features

- **Live radar view** — a rotating-sweep polar plot where every nearby BLE device appears as a colored dot, distance-scaled from center.
- **Signal-quality color coding** — dots and console labels are colored Strong / Good / Weak / Very Weak based on RSSI, so you can tell healthy signals from fading ones at a glance.
- **Distance estimation** — RSSI converted to meters using the log-distance path-loss model, smoothed with a rolling average per device to cut down on jitter.
- **Stable device placement** — each device's position is hashed from its MAC address, so it stays put between scans instead of jumping around randomly.
- **Live console table** — closest-first list with device name, RSSI, a signal-strength bar, and estimated distance, refreshed every second.
- **Closest-device callout** — always know what's nearest without scanning the table yourself.
- **Optional CSV logging** — pass `--csv` to append every detection (timestamp, address, name, RSSI, distance) to a file for later analysis.
- **Fully configurable via CLI** — tune TX power, path-loss exponent, radar range, device timeout, and smoothing without touching the code.
- **Fully offline & private** — no accounts, no network calls beyond your own Bluetooth adapter, no analytics.

---

## Tech Stack

| Technology | Purpose |
|-----------------------------|------------------------------------------------|
| Python 3.9+ | Core scanning, distance math, CLI |
| Bleak | Cross-platform BLE scanning (Windows / macOS / Linux) |
| Matplotlib | Live-animated polar "radar" plot |
| threading + asyncio | Background BLE scan loop, decoupled from the plot's main thread |
| CSV (stdlib, optional) | Local, append-only detection log — no database required |

---

## Core Functionality

### Detecting a device
- Bleak's `BleakScanner` listens for BLE advertisements in a background thread with its own asyncio event loop.
- Each detection (MAC address, name, RSSI) updates a shared, thread-safe device registry.

### Estimating distance
- RSSI is smoothed with a rolling average (`--smoothing`, default 5 samples) to reduce jitter.
- Distance is computed with the log-distance path-loss model:
  `distance = 10 ^ ((tx_power − rssi) / (10 × path_loss_exponent))`

### Rendering the radar
- Every second, Matplotlib's `FuncAnimation` re-reads the device snapshot, assigns each device its stable hashed angle, colors it by signal quality, and redraws the sweep line and dots.
- Devices not seen within `--timeout` seconds are dropped from the registry and disappear from the display.

---

## Getting Started

### 1. Install dependencies
```bash
pip install -r requirements.txt
# or directly:
pip install bleak matplotlib
```

### 2. Run it
```bash
python bluetooth.py
```

### 3. (Optional) Run with custom settings
```bash
python bluetooth.py --tx-power -62 --path-loss 3.0 --range 15 --csv session_log.csv
```

> **Linux users:** BLE scanning goes through BlueZ. If scanning fails silently, try running with `sudo`, or add your user to the `bluetooth` group and re-login.

---

## Calibration (for better accuracy)

The default numbers are reasonable guesses, not measurements of your specific environment. To tighten accuracy:

1. **Calibrate `--tx-power`** — place a known device exactly 1 meter away, read its RSSI from Pulse's console table, and pass that value: e.g. `--tx-power -58`.
2. **Tune `--path-loss`** for your space:
   | Environment | Suggested value |
   |---|---|
   | Open outdoor / free space | `2.0` |
   | Typical indoor room, few obstacles | `2.5` – `3.0` *(default: 2.5)* |
   | Indoor, many walls/obstacles | `3.5` – `4.0` |
3. **Increase `--smoothing`** if readings feel jumpy (higher = smoother but slower to react to real movement).

---

## Customization Tips

All tuning is CLI-first — no code edits needed for day-to-day use:

| Flag | Default | What it does |
|---|---|---|
| `--tx-power` | `-59` | Reference RSSI (dBm) at 1 meter — calibrate this first |
| `--path-loss` | `2.5` | Path-loss exponent for your environment |
| `--range` | `20` | Outer ring of the radar, in meters |
| `--timeout` | `8` | Seconds before a device drops off the radar |
| `--interval` | `1.0` | Seconds between scan/prune cycles |
| `--smoothing` | `5` | Rolling-average window size for RSSI |
| `--csv` | *(none)* | Path to append a live CSV detection log |

Want to go further?
- **Change the color bands**: edit `QUALITY_BANDS` near the top of `bluetooth.py` to shift what counts as Strong/Good/Weak.
- **Change the radar theme**: colors are set where the figure and axes are created (`fig.patch.set_facecolor`, `ax.set_facecolor`, line/scatter colors) — swap the hex codes for a different palette.
- **Feed it into hardware**: the `RadarState.snapshot()` method returns plain dicts (name, RSSI, distance, angle) — easy to redirect into a display, a Discord bot, or your own dashboard instead of Matplotlib.

---

## Limitations

- Distance is an **estimate**, not a measurement — RSSI is affected by walls, device orientation, your own body, and interference.
- Angle is **cosmetic**, not directional — Bluetooth doesn't provide bearing information without specialized hardware (UWB, AoA/AoD antenna arrays).
- Different devices transmit at different power levels, so absolute distance comparisons *between* different device types will be less reliable than tracking one device's trend over time.

---

## License

This project is released under the **MIT License** — free to use, modify, and share.
See the `LICENSE` file for details.

---

## Author

<p align="center">
  <a href="mailto:dhrubamajumder@proton.me" target="_blank">
    <img src="https://img.shields.io/badge/Email-Dhruba%20Majumder-blue?logo=gmail" alt="Email Badge">
  </a>
  <a href="https://www.linkedin.com/in/iamdhrubamajumder/" target="_blank">
    <img src="https://img.shields.io/badge/LinkedIn-Dhruba%20Majumder-blue?logo=linkedin" alt="LinkedIn Badge">
  </a>
  <a href="https://github.com/D-Majumder" target="_blank">
    <img src="https://img.shields.io/badge/GitHub-D--Majumder-black?logo=github" alt="GitHub Badge">
  </a>
</p>
