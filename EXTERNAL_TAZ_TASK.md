# Task: Add External TAZs to Downtown Calgary SUMO Model

## Context

This project simulates traffic in downtown Calgary using SUMO. The current OD
model treats downtown as a **closed system** — all trips are internal→internal
(between 12 internal zones). This systematically under-charges major corridors
(Macleod Trail, Memorial Drive, 16th Ave) because traffic originating outside
downtown is missing entirely.

## Goal

Add 4-8 **external TAZs** representing areas outside downtown (suburbs, Chinook
Centre, YYC airport, etc.). These zones generate trips that enter and leave
downtown through boundary edges, properly loading the entry/exit corridors.

## Project Location & Environment

- **Root:** `~/Documents/Calgary-SUMO/` (all scripts run from here, NOT from `od/`)
- **Network:** `calgary_downtown.net.xml` (75 MB, 113k edges, UTM zone 11U)
- **SUMO_HOME:** `/usr/share/sumo`
- **Python:** `~/Documents/Calgary-SUMO/.venv/bin/python3` — has `sumolib`, `pyproj`
  (If that venv doesn't exist, use `python3` and `import sys; sys.path.insert(0, '/usr/share/sumo/tools')`)
- **Downtown bbox:** LAT 51.03–51.08, LON -114.10 to -114.04
- **Machine:** ~7 GB RAM — the 75 MB network can be loaded but be careful

## Existing Pipeline (DO NOT modify these files — create NEW scripts)

1. `od/01_create_taz.py` — creates 12 internal TAZs (4×3 grid)
2. `od/02_gravity_model.py` — doubly-constrained gravity model (Furness/IPF)
3. `od/03a_simplify_taz.py` — keeps top-15 edges per zone by AADT
4. `od/03b_generate_trips.py` — generates trips from OD matrix
5. `od/04_compare_aadt.py` — compares assigned volumes to AADT (GEH metric)
6. `od/05_calibrate.py` — iterative OD calibration (gradient correction)

## Existing Data Files

- `aadt/edge_volumes.csv` — 144 AADT-matched edges (columns: `edge_id,aadt_volume,section_name`)
- `od/zone_stats.json` — 12 internal zones with production/attraction/centroid
- `od/od_matrix.json` — gravity-model OD matrix (12×12)
- `od/taz_simple.add.xml` — simplified TAZ file (12 zones, ~15 edges each)
- `od/od_matrix_calibrated.json` — calibrated matrix after 5 iterations

## Technical Approach

### Step 1: Create `od/01a_external_taz.py`

Find boundary edges and create external TAZs.

**How to find boundary edges:**
The downtown network was cut from the full Calgary network using
`--keep-edges.in-geo-boundary`. Edges at the boundary became "dangling" edges
connected to dead-end nodes. To find them:

1. Load the network with `sumolib.net.readNet('calgary_downtown.net.xml')`
2. For each node, count connected edges (node degree). Use `node.getOutgoing()`
   and `node.getIncoming()` from sumolib.
3. **Boundary nodes** = nodes with very low connectivity at the geographic edge
   of the bbox (within ~0.005° of a bbox boundary line). These are where the
   network was cut.
4. Alternatively, and more robustly: for each edge, get its midpoint in lat/lon
   via `net.convertXY2LonLat()`. If the midpoint is within ~0.003° of any bbox
   edge AND the edge connects to a dead-end-ish node (degree ≤ 2), it's a
   boundary edge.

**Sectors:** Group boundary edges by compass direction from downtown center
(center = LAT 51.055, LON -114.07):
- N, S, E, W (4 sectors minimum)
- Optionally NE, SE, SW, NW if there's enough data
- Use the bearing from center to edge midpoint: 
  `atan2(lon_diff, lat_diff)` → classify into octants

**Naming:** `ext_N`, `ext_S`, `ext_E`, `ext_W`, etc.

**Demand estimation per external zone:**
For each sector:
1. Collect all boundary edges in that sector
2. Sum their AADT values (from `aadt/edge_volumes.csv`) if available
3. If no AADT data for boundary edges in a sector, estimate from the nearest
   AADT edges in the internal zone adjacent to that sector
4. Total daily volume × PHF (0.09) = peak-hour volume
5. Split 50/50: production (inbound) and attraction (outbound)

**Output:**
- `od/taz_external.add.xml` — SUMO `<taz>` entries with tazSource/tazSink
  for the boundary edges of each external zone
- `od/external_zones.json` — zone metadata (name, centroid, production,
  attraction, boundary edges, total AADT)

### Step 2: Create `od/02a_gravity_model_extended.py`

Extend the gravity model to include external zones.

- Load internal zones from `od/zone_stats.json` and external zones from
  `od/external_zones.json`
- Build combined distance matrix (internal + external centroids)
- Apply gravity model with the SAME deterrence function `exp(-0.5 * d)`
- **External-Internal (EI):** External zones produce trips going into internal
  zones. External P and A drive these flows.
- **Internal-External (IE):** Internal zones produce trips going to external
  zones (mirror of EI).
- **External-External (EE) through trips:** Add ~30% of external production as
  through-trips that pass through downtown (e.g., N→S, E→W). These are critical
  for loading corridors like Macleod Trail.
- **Intra-external:** Zero (no intra-zone trips for external zones)
- Run Furness/IPF balancing on the full matrix
- Output: `od/od_matrix_extended.json`

### Step 3: Create `od/03c_generate_trips_extended.py`

Generate trips from the extended OD matrix.

- Read `od/od_matrix_extended.json`
- Read TAZ edges from BOTH `od/taz_simple.add.xml` (internal) and
  `od/taz_external.add.xml` (external)
- Same trip generation logic as `03b_generate_trips.py`
- Output: `od/od_trips_extended.xml`

### Step 4: Route + Compare

- Run duarouter:
  ```
  duarouter -n calgary_downtown.net.xml \
    -r od/od_trips_extended.xml \
    -o od/calgary_od_extended.rou.xml \
    --ignore-errors --no-step-log --no-warnings \
    --routing-algorithm dijkstra
  ```
- Run `od/04_compare_aadt.py` modified to read the new routes file, OR write
  a new comparison script `od/04a_compare_extended.py`

### Step 5: Create `od/05a_calibrate_extended.py`

Calibration loop on the extended matrix. Same gradient correction as
`05_calibrate.py` but operating on the larger matrix. Run 5-10 iterations.

## Validation Criteria

After implementation, run the full pipeline and report:
- Total trips (should increase vs. internal-only, which was ~38k)
- GEH < 5 percentage (was 9.7% → 17.4% after calibration)
- Mean GEH, RMSE, correlation
- Specifically check: Memorial Drive and Macleod Trail edges — are they now
  better loaded? (these were systematically under-assigned)

## Constraints

- **RAM:** Don't load the network multiple times in one script if avoidable.
  Load once, extract everything needed, then release.
- **External TAZ edges:** Only include edges that allow `passenger` vehicles.
  Use `edge.allows('passenger')` or check lane permissions.
- **If a sector has zero boundary edges or zero AADT:** Skip it (don't create
  an empty zone).
- **Time:** Keep total runtime under ~15 minutes for the full pipeline
  (including duarouter which takes ~2 min per routing pass).

## Deliverables

1. Scripts: `01a_external_taz.py`, `02a_gravity_model_extended.py`,
   `03c_generate_trips_extended.py`, `04a_compare_extended.py`,
   `05a_calibrate_extended.py` (all in `od/`)
2. Data: `taz_external.add.xml`, `external_zones.json`, `od_matrix_extended.json`,
   `od_trips_extended.xml`, calibrated output
3. A summary printed at the end of the calibration showing before/after metrics
4. Update `od/README.md` to document the external TAZ approach

## How to Run

All commands run from `~/Documents/Calgary-SUMO/`:
```bash
export SUMO_HOME=/usr/share/sumo
python3 od/01a_external_taz.py
python3 od/02a_gravity_model_extended.py
python3 od/03c_generate_trips_extended.py
# Route
/usr/share/sumo/bin/duarouter -n calgary_downtown.net.xml \
  -r od/od_trips_extended.xml -o od/calgary_od_extended.rou.xml \
  --ignore-errors --no-step-log --no-warnings --routing-algorithm dijkstra
# Compare
python3 od/04a_compare_extended.py
# Calibrate (5-10 iterations, each ~2min)
python3 od/05a_calibrate_extended.py
```
