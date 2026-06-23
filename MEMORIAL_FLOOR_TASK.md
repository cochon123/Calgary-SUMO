# Task: Floor on E↔W Through-Trips + Fix Calibration

## Problem

After 5 calibration iterations, Memorial Drive (the highest-AADT corridor at
88k vehicles/day, peak hour 7920) has **0 assigned vehicles**. This is not
imprecision — it's a functional blind spot. The calibration reduced E↔W
through-trips too aggressively (from 2286 to oscillating values), and the
trips that exist don't route through Memorial.

Two root causes:
1. **Demand level**: Initial matrix ratio was 1.71 (too high). Calibration
   oscillated trying to fix this, wasting iterations.
2. **No floor**: Through-trips E↔W can be calibrated to zero, removing the
   only demand source that loads E-W arterials like Memorial Drive.

## Fix (3 changes to `05a_calibrate_extended.py`)

### Change 1: Pre-scale the initial matrix

Before the calibration loop, scale the ENTIRE matrix by 0.585 (= 1/1.71)
to bring the initial ratio from 1.71 to ~1.0. This eliminates the need for
the dampening hack.

### Change 2: Enforce a floor on E↔W through-trips

After each calibration iteration's zone-factor corrections AND after any
normalization, enforce:

```python
FLOOR_EW = 4000  # ext_E -> ext_W minimum
FLOOR_WE = 4000  # ext_W -> ext_E minimum

idx_E = zones.index("ext_E")
idx_W = zones.index("ext_W")

T[idx_E][idx_W] = max(T[idx_E][idx_W], FLOOR_EW)
T[idx_W][idx_E] = max(T[idx_W][idx_E], FLOOR_WE)
```

This ensures at least 8000 total E↔W through-trips exist in every iteration.
Memorial Drive is one of several E-W corridors; with 8000 total E-W trips,
a fraction should naturally route through Memorial via Dijkstra.

### Change 3: Remove dampening, restore gentle normalization

Remove the `current_ratio > 1.5` dampening block. Instead, after zone-factor
corrections, normalize the total demand to the pre-scaled target (which is
now correct at ~0.585 × original). Use a gentle normalization that preserves
the zone-factor corrections:

```python
# Normalize to pre-scaled target (preserves relative corrections)
total = sum(T[i][j] for i in range(N) for j in range(N) if i != j)
target = pre_scaled_target  # = original_total * 0.585
if total > 0:
    scale = target / total
    # Apply floor AFTER normalization so it can't be scaled away
    for i in range(N):
        for j in range(N):
            if i != j:
                T[i][j] *= scale
    # Now enforce floors
    T[idx_E][idx_W] = max(T[idx_E][idx_W], FLOOR_EW)
    T[idx_W][idx_E] = max(T[idx_W][idx_E], FLOOR_WE)
```

## How to implement

Edit `od/05a_calibrate_extended.py`:

1. After loading the matrix (line ~133 where `T` is created from `matrix["T"]`),
   apply pre-scaling:
   ```python
   PRE_SCALE = 0.585
   for i in range(N):
       for j in range(N):
           T[i][j] *= PRE_SCALE
   pre_scaled_target = sum(T[i][j] for i in range(N) for j in range(N) if i != j)
   ```

2. Replace the dampening block (the `current_ratio > 1.5` section) with:
   ```python
   # Normalize to pre-scaled target
   total = sum(T[i][j] for i in range(N) for j in range(N) if i != j)
   if total > 0:
       scale = pre_scaled_target / total
       for i in range(N):
           for j in range(N):
               if i != j:
                   T[i][j] *= scale
   # Enforce through-trip floors (Memorial Drive loading)
   idx_E = zones.index("ext_E")
   idx_W = zones.index("ext_W")
   T[idx_E][idx_W] = max(T[idx_E][idx_W], 4000.0)
   T[idx_W][idx_E] = max(T[idx_W][idx_E], 4000.0)
   ```

3. Remove the `target_total` variable and its usage (it referenced the
   un-scaled original, which is wrong now).

4. Keep everything else the same (zone-factor corrections, GEH computation,
   routing, etc.).

5. After all iterations, the final routing pass should also use the floored
   matrix.

## After running

Print a specific Memorial Drive check:
- For each of these edges, print observed vs assigned volume and GEH:
  - `-24962865#2` (MEMOR8C, AADT=88000)
  - `292896517` (MEMOR9A, AADT=30000)
  - `-171068183` (MEMOR10, AADT=29000)
  - `149621572#2` (MEMOR9, AADT=27000)

Also print the standard metrics table and save to JSON.

## Validation

- If Memorial Drive edges have **>0 assigned vehicles** after calibration,
  the fix worked.
- If GEH<5 improves over 14.6% (the previous extended result), great.
- If correlation stays around 0.50-0.55, that's fine.

## How to run

```bash
cd ~/Documents/Calgary-SUMO
export SUMO_HOME=/usr/share/sumo
python3 od/05a_calibrate_extended.py
```

This takes ~25 minutes (5 iterations × ~5 min each including routing).
