# Task: WebSocket + TraCI Live Streaming (Option B — Edge/TLS State)

## CRITICAL ARCHITECTURE CONSTRAINTS — READ THESE FIRST

These constraints are NON-NEGOTIABLE. Do not deviate.

### Constraint 1: Thread Architecture

TraCI is synchronous and single-threaded. SUMO blocks on `traci.simulationStep()`.
Flask-SocketIO runs its own event loop. They CANNOT share a thread naively.

**MUST use**: `socketio.start_background_task()` to launch the TraCI simulation loop.
**MUST NOT use**: `threading.Thread`, `asyncio`, `multiprocessing`, or manual threading.

The pattern:
```python
socketio = SocketIO(app, cors_allowed_origins="*")

@socketio.on('start_simulation')
def handle_start(data):
    socketio.start_background_task(run_traci_simulation, plan_name=data.get('plan'))
```

The background function calls `socketio.emit()` every N steps to push updates to
connected clients. This is the ONLY correct way to bridge TraCI's blocking loop
with SocketIO's event loop.

### Constraint 2: Stream Edge/TLS State (Option B), NOT Vehicle Positions

**MUST stream**: `traci.edge.getLastStepVehicleNumber()` on top 50 edges +
`traci.trafficlight.getPhase()` on 16 Macleod TLS, every 10 steps.

**MUST NOT stream**: Individual vehicle positions (`traci.vehicle.getPosition()`).
Do not even call that function. It would be 5700 coords × 3600 steps = 20M emissions.

### Constraint 3: Payload Format

Every 10 steps, emit this JSON via `socketio.emit('sim_update', payload)`:

```json
{
    "step": 1420,
    "edges": {"620286625": 12, "222970218#0": 8, ...},
    "tls": {"cluster_30727620...": 2, "2160011261": 0, ...},
    "metrics": {"completed": 23, "mean_speed": 8.3, "total_vehicles": 156}
}
```

- `edges`: dict of edge_id → vehicle count (only top 50 edges, pre-determined)
- `tls`: dict of tls_id → current phase index (0-5 typically)
- `metrics`: completed trips, mean speed across all vehicles, total active vehicles

Total payload: < 2KB. If it's bigger, you're doing it wrong.

### Constraint 4: Frontend Updates

Leaflet receives the payload and updates existing layers via `setStyle()`.
**MUST NOT** re-render the map or recreate GeoJSON layers. Just update the
style of existing layers in-place.

For the performance chart: use a simple SVG polyline or canvas in the sidebar.
Do NOT add Chart.js or any external charting library — keep it dependency-free.
Just track an array of {step, mean_speed, completed} values and redraw a small
SVG path each update.

## Context

Project lives at: `/home/cochonhome/Documents/Calgary-SUMO/`

Existing files to read first:
- `viz/server.py` — current static Flask server (65 lines, simple)
- `viz/index.html` — current Leaflet dark theme UI (379 lines)
- `traci_corridor.py` — the TraCI simulation loop to adapt
- `output/top_tls.json` — Macleod corridor: 16 TLS IDs

Environment:
- `SUMO_HOME=/usr/share/sumo`
- Python 3.11 via system python3
- `sumolib` and `traci` importable via `sys.path.insert(0, os.path.join(SUMO_HOME, "tools"))`
- Flask already installed. You need to `pip install flask-socketio` (system or user install).
- Network: `calgary_downtown.net.xml` (75 MB, UTM zone 11U)
- Routes: `od/calgary_od_extended_calibrated.rou.xml` (12.9 MB)
- Scale: use `--scale 0.3` (≈5700 vehicles)

## What to build

### File 1: `viz/server_live.py`

A new Flask-SocketIO server that:
1. Serves the same static files as `server.py` (GeoJSON, HTML, metrics)
2. Accepts WebSocket connections
3. Has a `start_simulation` event handler that launches TraCI in a background task
4. Streams updates every 10 steps via `socketio.emit('sim_update', payload)`

The TraCI loop should:
1. Load the top 50 edge IDs from `viz/edges.geojson` (sorted by volume, pre-filtered)
2. Load the 16 Macleod TLS IDs from `output/top_tls.json`
3. Start SUMO with `--scale 0.3`, `--no-step-log`, `--no-warnings`
4. Every 10 steps, collect edge counts, TLS phases, and metrics, then emit
5. Also emit a `sim_end` event when simulation completes with final tripinfo stats
6. Apply the signal plan specified in the `start_simulation` event (default: baseline)

Signal plans to support (reuse from traci_corridor.py logic):
- `baseline`: no modification
- `green_wave_ns`: cycle 75s, 45s NS / 24s EW, progressive offset at 50 km/h
- `random_offsets`: cycle 75s, 45s NS / 24s EW, random offsets

The server should track simulation state: `idle`, `running`, `completed`.
Reject `start_simulation` if already running (emit error event).

### File 2: Modified `viz/index.html`

Add to the existing HTML:
1. Socket.IO client script (`<script src="https://cdn.socket.io/4.5.4/socket.io.min.js">`)
2. A "Start Live Simulation" button in the sidebar
3. A plan selector dropdown (baseline / green_wave_ns / random_offsets)
4. WebSocket connection logic that:
   - Connects to the server on page load
   - Sends `start_simulation` when button is clicked
   - Listens for `sim_update` events
   - Updates edge colors via `layer.setStyle()` on the existing GeoJSON layers
   - Updates TLS marker colors based on phase (0-1 = green/yellow NS, 2-3 = green/yellow EW)
5. A small SVG performance chart (200×80px) in the sidebar showing:
   - mean_speed (blue line)
   - completed trips (green line, scaled to fit)
   - Updates in real-time as sim_update events arrive
6. A "Sim Status" indicator: idle / running / completed with step counter

### Edge color mapping (reuse existing logic)

```
0 vehicles  → #1a1a3e (dark blue, empty)
1-50        → #2196F3 (blue, light)
51-200      → #4CAF50 (green, flowing)
201-500     → #FF9800 (orange, congested)
>500        → #f44336 (red, jammed)
```

### TLS phase → color mapping

```
Phase 0 (NS green)  → #4CAF50 (green)
Phase 1 (NS yellow) → #FFC107 (yellow)
Phase 2 (EW green)  → #00BCD4 (cyan)
Phase 3 (EW yellow) → #FF9800 (orange)
Other               → #9E9E9E (grey)
```

## Implementation order

```bash
pip install flask-socketio  # or pip3 install --user flask-socketio
cd /home/cochonhome/Documents/Calgary-SUMO
python3 viz/server_live.py  # verify it starts and serves the page
# Then manually test via browser if possible
```

## Verification checklist

1. Server starts on port 5001 (use 5001 to avoid conflict with existing server.py on 5000)
2. Socket.IO client connects successfully (check browser console)
3. Clicking "Start" launches the simulation (check server logs)
4. `sim_update` events arrive every 10 steps (check browser console)
5. Edge colors change in real-time on the map
6. TLS markers change color based on phase
7. Performance chart updates with mean_speed and completed curves
8. Simulation completes and shows "completed" status
9. Server can accept a second simulation run after the first completes

## Things to watch out for

- `socketio.start_background_task()` takes a function and its args. The function
  runs in a thread managed by SocketIO's event loop. Inside it, you can call
  `socketio.emit()` directly — it's thread-safe by design.
- Do NOT call `traci.start()` in the main thread. Only inside the background task.
- Do NOT use `@app.route` for the simulation start — use `@socketio.on('start_simulation')`.
- The existing `server.py` stays untouched. Create `server_live.py` as a new file.
- If flask-socketio complains about missing `python-engineio` or `python-socketio`,
  install them too: `pip install flask-socketio python-engineio python-socketio`
- Use `eventlet` or `gevent` ONLY if the default threading mode doesn't work.
  Flask-SocketIO works with threading by default (no async needed). Try threading first.
