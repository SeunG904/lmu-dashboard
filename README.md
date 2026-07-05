# LMU Telemetry Dashboard

Le Mans Ultimate shared memory API reader, packaged as a standalone
dashboard app (not an overlay).

## Layout

- **Top bar**: POS (place/total, e.g. `3/14`) / LAP / LAST LAP / BEST LAP /
  CURR LAP (0.001s precision, refreshed at 20Hz - smoothed but not too
  jittery) / delta bar / S1, S2, S3
- **Standings (enlarged, at-a-glance)**: POS / NAME (short form, e.g.
  S.CHOI) / LAST / VE% / GAP / STATUS (PIT/OUT) / TIRE%
- **Track map (smaller)**: white racing line + perpendicular sector tick
  marks + local yellow flags + class-colored car dots, with track name,
  session best, air/track temp, and a wind compass overlay. **Freezes
  permanently once fully drawn** and now **persists to disk** so it's
  instant on every future run of the same track.
- **Tires**: matches the reference image - wear% (color-coded), a 3-way
  split temperature mini-bar (inner-layer readings, confirmed to match
  in-game values) with average degrees, pressure in **psi**, and last-lap
  wear, with the compound badge centered between the four corners
- **Fuel / VE**: FUEL (current + bar), EST LAPS, REFUEL (amount needed to
  finish the race), AVG 2/5/ALL-lap usage, LAST LAP FUEL. (FUEL TIME
  removed per request.) VE is summarized in a line below.
- **Onboard**: matches the reference image exactly - BRK BIAS (red),
  TC SLIP / TC CUT / TC (blue), ABS (yellow), MAP (green), single values
  (not x/max). Shows `-` for anything not applicable to the current car
  (e.g. MAP on a non-hybrid).
- **Sectors**: LAST vs AVG5 vs DELTA
- **Gap**: ahead / behind / position
- **Telemetry** (now sized to match TinyPedal-style prominence, swapped
  sizing with the fuel panel): speed as a big number, pedals
  (throttle/brake only, last 10s scrolling graph), and a Formula-style
  steering wheel icon that rotates using the car's real lock-to-lock
  range — now reading `mVisualSteeringWheelRange` (the in-cockpit 3D
  wheel's rotation) instead of `mPhysicalSteeringWheelRange` (your
  hardware's configured range), since those two can differ due to
  steering rack ratio. This matches your measured 292-293° full lock.

All UI text is in English now.

## 16:9 aspect ratio

The whole dashboard content is wrapped in a widget that always keeps a
16:9 aspect ratio, regardless of window size. Resizing or maximizing the
window no longer stretches individual panels out of shape — instead,
extra space becomes letterboxing (black bars) on the sides or top/bottom.

## Track map: freeze + permanent save

1. Recording is skipped entirely while in the pits or while the lap is
   invalidated (track limits), so those moments can't corrupt the map.
2. Once a normal lap fills 95%+ of the track, the map **locks** and stops
   updating for the rest of the session.
3. The finished map is **saved to `track_maps/<track_name>.json`**. On
   every future launch (even a new run of the app), if a saved map exists
   for the current track it's loaded instantly and locked immediately —
   you never have to "redraw" a track you've already mapped.

## Lap timer update rate

Back to 60Hz local interpolation for the current-lap display, so it
updates in real time down to 0.001s (like a real stopwatch) between the
20Hz data polls. Only the two lap-time text labels update this often —
the heavier panels (standings table, track map paint) still refresh at
20Hz, so this stays cheap.

## Building a standalone .exe (like TinyPedal) — two options

**I can't build a Windows .exe directly from here** (this sandbox is
Linux, and PyInstaller doesn't cross-compile between OSes). Two ways to
get a real one:

### Option 1 — GitHub Actions (no Windows PC needed)
A workflow is included at `.github/workflows/build.yml`. Push this
project to a GitHub repo, then go to the "Actions" tab and run "Build
Windows exe" (or just push to `main`). It builds on a real Windows
runner and uploads `LMU_Dashboard.exe` as a downloadable artifact —
this is the closest equivalent to how TinyPedal ships prebuilt releases,
and means anyone you share the repo with can also generate the exe
without owning Windows.

### Option 2 — build it yourself on Windows
On your own Windows machine, after `pip install -r requirements.txt`:

```bash
build_exe.bat
```

This installs PyInstaller and builds a single-file, windowed executable
at `dist\LMU_Dashboard.exe` — no Python installation needed to run it
afterward. (The PyInstaller dependency trace was verified to complete
cleanly in this sandbox; the actual Windows binary still has to be
produced on Windows.)

## Running from source

```bash
pip install -r requirements.txt
python main.py
```

- LMU Settings → Gameplay → **Enable Plugins** must be on (no extra DLL
  needed).
- Windows only for live game data (LMU itself is Windows-only, and the
  shared memory uses a Windows named mapping).
- Python 3.10–3.12 recommended (PySide6).

## Folder structure

```
lmu_dashboard/
├── main.py                  # entry point, 20Hz data + lap-timer timers, 16:9 wrapper
├── telemetry_reader.py       # shared memory reader + lap-based stats + track map persistence
├── dashboard_widgets.py      # standings/track map/tires/fuel/sectors/gap/onboard
├── graph_widgets.py          # speed number / pedal graph / F1 steering wheel / delta bar
├── top_bar.py                # top bar
├── build_exe.bat             # PyInstaller build script (run on Windows)
├── requirements.txt
├── requirements-dev.txt      # pyinstaller, only needed for building the exe
├── vendor/pyLMUSharedMemory/ # shared memory struct definitions (TinyPedal project)
├── track_maps/                # saved track maps appear here after first full lap
└── README.md
```

## Notes / current limits

- Tire pressure is now shown in **psi** (converted from the game's raw kPa).
- REFUEL is only computed for lap-based races (`mMaxLaps > 0`); time-based
  races show `--`.
- Standings VE%/TIRE% only appear if the game fills in that opponent's
  detailed telemetry — this may not happen for remote players in online
  multiplayer, in which case it shows `-`.
- Sector comparison only uses completed laps (no live mid-sector delta yet).
- Class colors are matched by substring ("hypercar"/"lmp2"/"lmp3"/"gt3")
  in `telemetry_reader.py`'s `CLASS_COLORS` — edit there if LMU reports
  different class name strings.
- On overall visual polish: PySide6 can go further than this (custom QSS
  theme, custom window chrome/title bar, icon fonts, subtle shadows). Happy
  to push further if there's a specific look you want beyond this.

## Credit

- Shared memory struct definitions: TinyPedal/pyLMUSharedMemory
  (https://github.com/TinyPedal/pyLMUSharedMemory)
