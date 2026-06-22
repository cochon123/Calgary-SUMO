#!/usr/bin/env python3
"""
Step 5: Iterative OD matrix calibration.

Method: Path-based gradient correction.
For each iteration:
  1. Route current OD trips → get edge volumes
  2. For each AADT edge: correction = observed / max(assigned, 1)
  3. For each OD pair: scale by average correction of AADT edges on its path
  4. Re-route and measure GEH

This is a simplified version of the OD estimation problem (cf. Cadyts).
"""
import xml.etree.ElementTree as ET
import json, csv, math, random, subprocess, os, sys
from collections import defaultdict

SUMO_HOME = os.environ.get('SUMO_HOME', '/usr/share/sumo')
NET_FILE = 'calgary_downtown.net.xml'
TAZ_FILE = 'od/taz_simple.add.xml'
MAX_ITERATIONS = 5

PHF = 0.09
SIM_END = 3600

random.seed(42)

# --- Load data ---------------------------------------------------------------
matrix = json.load(open('od/od_matrix.json'))
zones = matrix['zones']
T = matrix['T']
N = len(zones)

# Load AADT observations
edge_aadt = {}
with open('aadt/edge_volumes.csv') as f:
    for row in csv.DictReader(f):
        edge_aadt[row['edge_id']] = float(row['aadt_volume'])
edge_aadt_peak = {e: v * PHF for e, v in edge_aadt.items()}

# Load TAZ edges
taz_edges = {}
tree = ET.parse(TAZ_FILE)
for taz in tree.findall('.//taz'):
    zid = taz.get('id')
    sources = [(s.get('id'), float(s.get('weight', 1)))
               for s in taz.findall('tazSource')]
    if sources:
        taz_edges[zid] = sources

def weighted_choice(items):
    total = sum(w for _, w in items)
    r = random.uniform(0, total)
    cum = 0
    for eid, w in items:
        cum += w
        if r <= cum:
            return eid
    return items[-1][0]

def geh(m, c):
    if (m + c) < 1e-10:
        return 0
    return math.sqrt(2 * (m - c)**2 / (m + c))

def generate_trips(trips_file, matrix_T):
    """Generate trip definitions from OD matrix."""
    trip_id = 0
    with open(trips_file, 'w') as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<routes>\n')
        for i in range(N):
            for j in range(N):
                if i == j:
                    continue
                n_trips = int(round(matrix_T[i][j]))
                if n_trips < 1:
                    continue
                if zones[i] not in taz_edges or zones[j] not in taz_edges:
                    continue
                for k in range(n_trips):
                    src = weighted_choice(taz_edges[zones[i]])
                    dst = weighted_choice(taz_edges[zones[j]])
                    depart = random.uniform(0, SIM_END)
                    f.write(f'  <trip id="t{trip_id}" depart="{depart:.1f}" '
                            f'from="{src}" to="{dst}"/>\n')
                    trip_id += 1
        f.write('</routes>\n')
    return trip_id

def route_trips(trips_file, routes_file):
    """Run duarouter to compute routes."""
    cmd = [
        os.path.join(SUMO_HOME, 'bin', 'duarouter'),
        '-n', NET_FILE,
        '-r', trips_file,
        '-o', routes_file,
        '--ignore-errors',
        '--no-step-log',
        '--no-warnings',
        '--routing-algorithm', 'dijkstra',
    ]
    # Try standard path if bin/ doesn't exist
    if not os.path.exists(cmd[0]):
        cmd[0] = 'duarouter'
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return result.returncode == 0

def extract_volumes_and_paths(routes_file):
    """Extract edge volumes and OD-pair -> edge-paths mapping."""
    edge_vol = defaultdict(int)
    # For calibration: track which edges each trip uses
    # But we can't track individual trips back to OD pairs easily.
    # Instead, we track volumes and use edge-level corrections.

    context = ET.iterparse(routes_file, events=('end',))
    for event, elem in context:
        if elem.tag == 'vehicle':
            route = elem.find('route')
            if route is not None and route.get('edges'):
                edges = [e for e in route.get('edges').split() if ':' not in e]
                for eid in edges:
                    edge_vol[eid] += 1
            elem.clear()
    return edge_vol

def compute_metrics(edge_vol):
    """Compute calibration metrics."""
    import statistics
    comparisons = []
    for eid, obs in edge_aadt_peak.items():
        asn = edge_vol.get(eid, 0)
        comparisons.append((obs, asn))

    gehs = [geh(o, a) for o, a in comparisons]
    geh5 = sum(1 for g in gehs if g < 5)
    rmse = math.sqrt(sum((o - a)**2 for o, a in comparisons) / len(comparisons))

    obs_vals = [c[0] for c in comparisons]
    asn_vals = [c[1] for c in comparisons]
    mean_obs = statistics.mean(obs_vals)
    mean_asn = statistics.mean(asn_vals)

    mo, ma = statistics.mean(obs_vals), statistics.mean(asn_vals)
    num = sum((o - mo) * (a - ma) for o, a in comparisons)
    d1 = math.sqrt(sum((o - mo)**2 for o in obs_vals))
    d2 = math.sqrt(sum((a - ma)**2 for a in asn_vals))
    corr = num / (d1 * d2) if d1 * d2 > 0 else 0

    return {
        'geh5_pct': 100 * geh5 / len(comparisons),
        'mean_geh': statistics.mean(gehs),
        'rmse': rmse,
        'correlation': corr,
        'mean_obs': mean_obs,
        'mean_asn': mean_asn,
        'ratio': mean_asn / mean_obs if mean_obs > 0 else 0,
    }

# --- Calibration loop --------------------------------------------------------
print("=" * 70)
print("  ITERATIVE OD MATRIX CALIBRATION")
print("=" * 70)
print(f"  Iterations: {MAX_ITERATIONS}")
print(f"  AADT edges: {len(edge_aadt_peak)}")
print(f"  Zones: {N}")
print()

best_T = [row[:] for row in T]
history = []

for iteration in range(MAX_ITERATIONS):
    print(f"\n--- Iteration {iteration+1}/{MAX_ITERATIONS} ---")

    # 1. Generate trips from current matrix
    trips_file = f'od/iter_{iteration}_trips.xml'
    routes_file = f'od/iter_{iteration}_routes.xml'
    n_trips = generate_trips(trips_file, T)
    print(f"  Generated {n_trips} trips")

    # 2. Route trips
    print(f"  Routing...", end=' ', flush=True)
    ok = route_trips(trips_file, routes_file)
    if not ok:
        print("FAILED")
        break
    print("done")

    # 3. Extract volumes
    edge_vol = extract_volumes_and_paths(routes_file)

    # 4. Compute metrics
    metrics = compute_metrics(edge_vol)
    history.append(metrics)
    print(f"  GEH<5: {metrics['geh5_pct']:.1f}% | Mean GEH: {metrics['mean_geh']:.1f} | "
          f"RMSE: {metrics['rmse']:.0f} | R: {metrics['correlation']:.3f} | "
          f"Ratio: {metrics['ratio']:.2f}")

    # 5. Adjust OD matrix based on edge-level corrections
    # For each AADT edge, compute correction factor
    corrections = {}
    for eid, obs in edge_aadt_peak.items():
        asn = edge_vol.get(eid, 0)
        if asn > 0:
            # Correction: how much to scale traffic using this edge
            corrections[eid] = min(max(obs / asn, 0.3), 3.0)  # clip [0.3, 3.0]
        elif obs > 100:
            # Under-assigned edge with significant observed volume
            # Boost demand — find which TAZ this edge is in and increase
            corrections[eid] = 1.5  # moderate boost

    # For matrix adjustment: use the fact that edges near each TAZ contribute
    # to flows from/to that TAZ. Scale zone productions/attractions.
    # Simplified: scale entire OD matrix rows/cols based on zone-level correction.
    zone_correction = defaultdict(list)
    # Map edges to zones (from TAZ file)
    edge_to_zone = {}
    for zid in taz_edges:
        for eid, _ in taz_edges[zid]:
            edge_to_zone[eid] = zid

    for eid, corr in corrections.items():
        zid = edge_to_zone.get(eid)
        if zid:
            zone_correction[zid].append(corr)

    # Apply corrections to OD matrix
    zone_factors = {}
    for zid in zones:
        corrs = zone_correction.get(zid, [1.0])
        # Use median to be robust
        import statistics as stats
        zone_factors[zid] = stats.median(corrs)

    # Scale matrix: T[i][j] *= sqrt(factor_i * factor_j)
    for i in range(N):
        fi = zone_factors.get(zones[i], 1.0)
        for j in range(N):
            if i == j:
                continue
            fj = zone_factors.get(zones[j], 1.0)
            T[i][j] = T[i][j] * math.sqrt(fi * fj)

    # Normalize to maintain total demand level
    total_current = sum(T[i][j] for i in range(N) for j in range(N) if i != j)
    total_target = sum(matrix['T'][i][j] for i in range(N) for j in range(N) if i != j)
    if total_current > 0:
        scale = total_target / total_current
        T = [[T[i][j] * scale for j in range(N)] for i in range(N)]

    print(f"  Zone factors: {', '.join(f'{z[:6]}={zone_factors.get(z,1):.2f}' for z in zones[:6])}...")

# --- Final summary -----------------------------------------------------------
print(f"\n{'='*70}")
print(f"  CALIBRATION HISTORY")
print(f"{'='*70}")
print(f"  {'Iter':>4}  {'GEH<5%':>7}  {'Mean GEH':>8}  {'RMSE':>6}  {'R':>6}  {'Ratio':>6}")
for i, m in enumerate(history):
    print(f"  {i+1:>4}  {m['geh5_pct']:>6.1f}%  {m['mean_geh']:>8.1f}  "
          f"{m['rmse']:>6.0f}  {m['correlation']:>6.3f}  {m['ratio']:>6.2f}")

# Save best result
best_iter = max(range(len(history)), key=lambda i: history[i]['geh5_pct'])
print(f"\n  Best iteration: {best_iter+1} (GEH<5: {history[best_iter]['geh5_pct']:.1f}%)")

# Save final matrix
matrix['T'] = [[round(T[i][j]) for j in range(N)] for i in range(N)]
matrix['calibration_history'] = history
json.dump(matrix, open('od/od_matrix_calibrated.json', 'w'), indent=2)
print(f"  Calibrated matrix: od/od_matrix_calibrated.json")
