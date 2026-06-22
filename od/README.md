# OD-Based Traffic Assignment & Calibration

This directory implements **proper traffic assignment with AADT calibration**,
replacing the earlier (methodologically flawed) edge-based demand generation.

## The Problem with Edge-Based Demand

The previous approach (`aadt/generate_demand.py`) treated each AADT measurement
as an independent traffic generator — one vehicle per edge. But AADT measures
**flow traversing a link**, not trips originating there. A single vehicle
crosses 5–15 AADT measurement points during its trip, so counting each AADT
point independently inflates demand by that factor. The `--scale 0.05` hack
masked the total volume but produced a spatially incorrect distribution.

## The Correct Method

Standard transport engineering practice:

1. **Define TAZs** — partition the network into origin-destination zones
2. **Build OD matrix** — estimate trip flows between zone pairs (gravity model)
3. **Traffic assignment** — route OD flows onto the network (user equilibrium)
4. **Calibrate** — compare assigned edge volumes to AADT observations, adjust
   OD matrix iteratively until convergence

AADT serves as a **calibration constraint**, not a demand source.

## Pipeline

```
01_create_taz.py          → 12 TAZs (4×3 grid), zone productions from AADT/AVG_CROSSINGS
02_gravity_model.py       → Doubly-constrained OD matrix (Furness/IPF)
03a_simplify_taz.py       → Reduce TAZs to 15 edges/zone (memory optimization)
03b_generate_trips.py     → Convert OD matrix to 38k individual trips
04_compare_aadt.py        → Baseline calibration metrics (GEH, RMSE, R)
05_calibrate.py           → 5-iteration gradient calibration loop
```

## Results

| Metric | Iter 1 | Iter 2 | Iter 3 | Iter 4 | Iter 5 |
|--------|--------|--------|--------|--------|--------|
| GEH < 5 | 9.7% | 9.7% | 9.0% | 12.5% | **17.4%** |
| Mean GEH | 29.8 | 26.5 | 24.4 | 23.6 | **23.0** |
| RMSE | 1504 | 1284 | 1185 | 1150 | **1136** |
| Pearson R | 0.256 | 0.400 | 0.483 | 0.520 | **0.542** |
| Ratio | 0.96 | 0.89 | 0.85 | 0.83 | **0.81** |

### Interpretation

- **Correlation doubled** (0.26 → 0.54) in 5 iterations — the spatial
  distribution of traffic is converging toward reality.
- **GEH improving** but still below the 85% target. Remaining gap comes from:
  - Boundary effects (external traffic entering/leaving downtown not captured)
  - Only 144 AADT observation points for 113k edges
  - Zone-level correction (not edge-level) — path-based correction would improve
  - Single routing pass (Dijkstra) rather than UE equilibrium
- **Ratio drifting below 1.0** — the correction is pushing demand toward
  under-assigned edges, slightly over-correcting total volume.

### Run the calibrated simulation

```bash
export SUMO_HOME=/usr/share/sumo
cd ~/Documents/Calgary-SUMO

# Full pipeline
python3 od/01_create_taz.py        # Create TAZs
python3 od/02_gravity_model.py     # Build OD matrix
python3 od/03a_simplify_taz.py     # Simplify TAZs
python3 od/03b_generate_trips.py   # Generate trips
python3 od/05_calibrate.py         # Run calibration loop

# Simulate with calibrated routes
sumo -c calgary_od_calibrated.sumocfg
```

## Limitations & Next Steps

1. **More iterations** — convergence is still improving at iteration 5
2. **Path-based correction** — instead of zone-level factors, track which OD
   pairs' routes pass through each AADT edge
3. **External demand** — add boundary TAZs representing traffic entering from
   outside the downtown area (major highways, bridges)
4. **User equilibrium** — use `duaIterate.py` for true UE assignment instead
   of single-pass Dijkstra routing
5. **More AADT data** — the 2023 dataset has 326 city-wide points; additional
   years provide temporal validation
