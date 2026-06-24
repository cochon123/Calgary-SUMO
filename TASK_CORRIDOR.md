# Task: Multi-TLS Corridor Optimization (grid search, pre-RL)

## Context

This is a SUMO traffic simulation project for downtown Calgary. The project lives at:
`/home/cochonhome/Documents/Calgary-SUMO/`

Key files:
- Network: `calgary_downtown.net.xml` (75 MB, 113k edges, UTM zone 11U / EPSG:32611)
- Calibrated routes: `od/calgary_od_extended_calibrated.rou.xml` (12.9 MB, ~19k vehicles)
- SUMO config: `calgary_downtown.sumocfg`
- Existing single-TLS optimizer: `traci_optimize.py` (read this first!)
- Edge volumes GeoJSON: `viz/edges.geojson` (3480 features, each has `id`, `volume`, `aadt`, `type`)
- TLS points GeoJSON: `viz/tls_points.geojson` (383 features, each has `id`, `n_links`)
- TraCI results JSON: `output/traci_optimization_results.json`

Environment:
- `SUMO_HOME=/usr/share/sumo`
- Python: use the system `python3` (3.11). `sumolib` and `traci` are importable via `sys.path.insert(0, os.path.join(SUMO_HOME, "tools"))`. The Hermes venv at `~/.hermes/venv` also has `pyproj`.
- Machine: ~7 GB RAM. Each SUMO run of 3600 steps takes ~1-2 minutes. Keep grid search to ≤20 total runs.

## What to build (two scripts)

### Script 1: `identify_top_tls.py`

Identify the 10 most loaded traffic lights in the downtown network, then group them into corridors.

Method:
1. Load the SUMO network with `sumolib.net.readNet(NET_FILE)`.
2. For each TLS ID from `traci.trafficlight.getIDList()` (or parse from the network), get its controlled lanes → map to edges → sum the `volume` field from `viz/edges.geojson`. This gives a "load score" for each TLS.
3. Rank all 383 TLS by load score, output top 20.
4. Among the top, identify a CORRIDOR: a set of 5-10 TLS that are geographically aligned along one road (e.g., Macleod Trail N-S, or an E-W arterial like Memorial Drive / 16th Ave). Use the TLS coordinates from `tls_points.geojson` (which are already in lat/lon for Leaflet, converted from UTM). Group TLS that are within a narrow bearing range of each other and roughly sequential (sorted by one coordinate).
5. Output:
   - `output/top_tls.json` — list of top 20 TLS with their load scores, coordinates, and controlled edge volumes
   - Print a clear table showing the identified corridor (which road, which TLS, their order along the corridor, distances between consecutive intersections)
   - Print the corridor as a JSON list of TLS IDs in corridor order (this is the input for script 2)

IMPORTANT: reading the full 75 MB network with sumolib is fine but takes a few seconds. Cache results.

### Script 2: `traci_corridor.py`

Extend the single-TLS TraCI loop (from `traci_optimize.py`) to optimize ALL TLS in the identified corridor SIMULTANEOUSLY, and test coordination strategies via grid search.

The core insight: optimizing one TLS in isolation is myopic. When you release a platoon at intersection A, it arrives at intersection B — if B is red, you've just moved the queue, not eliminated it. A *green wave* (coordinated offsets) lets platoons flow through multiple intersections without stopping.

Grid search plans to test:
1. **baseline**: original programs from the network (no modification)
2. **uniform_short**: all corridor TLS get cycle 60s, balanced split (27s NS / 27s EW / 3s yellow each), zero offset
3. **uniform_long**: all corridor TLS get cycle 90s, balanced split (42s NS / 42s EW / 3s yellow), zero offset
4. **green_wave_ns**: cycle 75s, favor NS direction (45s NS / 24s EW / 3s yellow), with progressive offset so NS-bound platoons hit green at each successive intersection. Compute offset from distance between intersections and an assumed free-flow speed (50 km/h = ~14 m/s).
5. **green_wave_ew**: same but favor EW direction with progressive EW offset.
6. **green_wave_ns_fast**: same as 4 but assume 60 km/h free-flow speed for offset calculation.
7. **random_offsets**: cycle 75s, same split as wave plans, but random offsets (control — tests whether coordination matters vs just having good splits).

For each plan:
- Apply the phase program to ALL corridor TLS simultaneously (modify `traci_optimize.py`'s `setProgramLogic` to loop over the corridor TLS list).
- For green wave plans: after setting the program, set each TLS's phase offset using `traci.trafficlight.setPhase(tls_id, offset_phase_index)` at simulation start, OR by setting the `program.subParameter` / shifting the `currentPhaseIndex`. The standard SUMO approach: give each TLS the same program but start it at a different phase index to create the offset progression.
- Run 3600 steps, parse `tripinfo.xml` for ALL vehicles (not just corridor vehicles — measure NETWORK-WIDE effect, because moving a queue from one intersection can create congestion elsewhere).
- Also track corridor-specific metrics: for vehicles that pass through ANY corridor TLS lane, compute their average travel time separately.

Metrics to report per plan:
- n_trips, n_completed
- avg_duration (network-wide), avg_wait, avg_time_loss
- corridor_avg_duration (vehicles touching corridor), corridor_n_vehicles
- total_waiting_time

Output:
- `output/corridor_optimization_results.json` — all plans + metrics + corridor TLS list + plan definitions
- Print a comparison table sorted by avg_duration, showing delta vs baseline
- Print a clear verdict: did coordination (green wave) beat uniform splits? Did it beat baseline?

## Constraints

- Each TLS program must be a valid 4-phase program: [NS green, NS yellow, EW green, EW yellow]. Read the existing TLS programs from the network first to get the actual phase structure — some TLS may have more phases (protected turns, etc.). If a TLS has more than 4 phases, adapt: identify the NS-green and EW-green phases and only modify those durations, keeping other phases as-is. If this is too complex for some TLS, skip that TLS and log a warning.
- The grid search has ≤7 plans × 3600 steps each. Budget ~15 minutes total runtime.
- Do NOT modify `traci_optimize.py` — create `traci_corridor.py` as a new file that imports the `parse_tripinfo` function from it.
- Use `--no-warnings --no-step-log` to keep output clean.
- Run script 1 first, verify the corridor output, THEN run script 2.

## Execution order

```bash
cd /home/cochonhome/Documents/Calgary-SUMO
python3 identify_top_tls.py    # → outputs top TLS + corridor
python3 traci_corridor.py      # → reads corridor, runs grid search
```

## Verification

After running both scripts, verify:
1. `identify_top_tls.py` produced a corridor of 5-10 TLS on a recognizable Calgary road
2. `traci_corridor.py` completed all 7 plans without errors
3. The results JSON has meaningful deltas (not all zeros)
4. The verdict clearly states whether green-wave coordination helped
