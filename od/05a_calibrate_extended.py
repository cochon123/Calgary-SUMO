#!/usr/bin/env python3
"""Five-iteration calibration loop for the extended OD matrix."""
import csv
import json
import math
import os
import random
import statistics
import subprocess
import xml.etree.ElementTree as ET
from collections import defaultdict

SUMO_HOME = os.environ.get("SUMO_HOME", "/usr/share/sumo")
NET_FILE = "calgary_downtown.net.xml"
MATRIX_FILE = "od/od_matrix_extended.json"
TAZ_FILES = ["od/taz_simple.add.xml", "od/taz_external.add.xml"]
MAX_ITERATIONS = 5
PHF = 0.09
SIM_END = 3600
random.seed(42)


def load_taz_edges():
    edges = {}
    for path in TAZ_FILES:
        tree = ET.parse(path)
        for taz in tree.findall(".//taz"):
            zid = taz.get("id")
            sources = [(s.get("id"), float(s.get("weight", 1))) for s in taz.findall("tazSource")]
            if sources:
                edges[zid] = sources
    return edges


def weighted_choice(items):
    total = sum(w for _, w in items)
    r = random.uniform(0, total)
    acc = 0.0
    for eid, weight in items:
        acc += weight
        if r <= acc:
            return eid
    return items[-1][0]


def generate_trips(path, matrix_T, zones, taz_edges):
    trip_id = 0
    with open(path, "w") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n<routes>\n')
        for i, origin in enumerate(zones):
            for j, dest in enumerate(zones):
                if i == j:
                    continue
                n = int(round(matrix_T[i][j]))
                if n < 1 or origin not in taz_edges or dest not in taz_edges:
                    continue
                for _ in range(n):
                    src = weighted_choice(taz_edges[origin])
                    dst = weighted_choice(taz_edges[dest])
                    if src == dst:
                        continue
                    f.write(f'  <trip id="t{trip_id}" depart="{random.uniform(0, SIM_END):.1f}" from="{src}" to="{dst}"/>\n')
                    trip_id += 1
        f.write("</routes>\n")
    return trip_id


def route_trips(trips_file, routes_file):
    exe = os.path.join(SUMO_HOME, "bin", "duarouter")
    if not os.path.exists(exe):
        exe = "duarouter"
    cmd = [
        exe, "-n", NET_FILE, "-r", trips_file, "-o", routes_file,
        "--ignore-errors", "--no-step-log", "--no-warnings",
        "--routing-algorithm", "dijkstra",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        print(result.stderr[-2000:])
    return result.returncode == 0


def extract_volumes(routes_file):
    edge_vol = defaultdict(int)
    context = ET.iterparse(routes_file, events=("end",))
    for _, elem in context:
        if elem.tag == "vehicle":
            route = elem.find("route")
            if route is not None and route.get("edges"):
                for eid in route.get("edges").split():
                    if ":" not in eid:
                        edge_vol[eid] += 1
            elem.clear()
    return edge_vol


def geh(obs, asn):
    if obs + asn < 1e-10:
        return 0.0
    return math.sqrt(2 * (obs - asn) ** 2 / (obs + asn))


def compute_metrics(edge_vol, edge_aadt_peak):
    pairs = [(obs, edge_vol.get(eid, 0)) for eid, obs in edge_aadt_peak.items()]
    gehs = [geh(o, a) for o, a in pairs]
    obs_vals = [p[0] for p in pairs]
    asn_vals = [p[1] for p in pairs]
    mean_obs = statistics.mean(obs_vals)
    mean_asn = statistics.mean(asn_vals)
    denom = math.sqrt(sum((o - mean_obs) ** 2 for o in obs_vals) * sum((a - mean_asn) ** 2 for a in asn_vals))
    corr = sum((o - mean_obs) * (a - mean_asn) for o, a in pairs) / denom if denom else 0.0
    return {
        "geh5_pct": 100 * sum(1 for g in gehs if g < 5) / len(gehs),
        "mean_geh": statistics.mean(gehs),
        "rmse": math.sqrt(sum((o - a) ** 2 for o, a in pairs) / len(pairs)),
        "correlation": corr,
        "mean_obs": mean_obs,
        "mean_asn": mean_asn,
        "ratio": mean_asn / mean_obs if mean_obs else 0.0,
    }


def write_final_routes(matrix_T, zones, taz_edges):
    trips = "od/od_trips_extended_calibrated.xml"
    routes = "od/calgary_od_extended_calibrated.rou.xml"
    n = generate_trips(trips, matrix_T, zones, taz_edges)
    ok = route_trips(trips, routes)
    return n, routes if ok else None


matrix = json.load(open(MATRIX_FILE))
zones = matrix["zones"]
T = [[float(v) for v in row] for row in matrix["T"]]
N = len(zones)
taz_edges = load_taz_edges()

edge_aadt_peak = {}
with open("aadt/edge_volumes.csv", newline="") as f:
    for row in csv.DictReader(f):
        edge_aadt_peak[row["edge_id"]] = float(row["aadt_volume"]) * PHF

edge_to_zone = {}
for zid, edges in taz_edges.items():
    for eid, _ in edges:
        edge_to_zone[eid] = zid

target_total = sum(T[i][j] for i in range(N) for j in range(N) if i != j)
history = []

print("=" * 70)
print("  EXTENDED OD MATRIX CALIBRATION")
print("=" * 70)
print(f"Iterations: {MAX_ITERATIONS}")
print(f"Zones: {N}")
print(f"AADT edges: {len(edge_aadt_peak)}")

for iteration in range(MAX_ITERATIONS):
    print(f"\n--- Iteration {iteration + 1}/{MAX_ITERATIONS} ---")
    trips_file = f"od/iter_ext_{iteration}_trips.xml"
    routes_file = f"od/iter_ext_{iteration}_routes.xml"
    n_trips = generate_trips(trips_file, T, zones, taz_edges)
    print(f"  Generated {n_trips} trips")
    print("  Routing...", end=" ", flush=True)
    if not route_trips(trips_file, routes_file):
        print("FAILED")
        break
    print("done")
    edge_vol = extract_volumes(routes_file)
    metrics = compute_metrics(edge_vol, edge_aadt_peak)
    history.append(metrics)
    print(
        f"  GEH<5: {metrics['geh5_pct']:.1f}% | Mean GEH: {metrics['mean_geh']:.1f} | "
        f"RMSE: {metrics['rmse']:.0f} | R: {metrics['correlation']:.3f} | Ratio: {metrics['ratio']:.2f}"
    )

    corrections = {}
    for eid, obs in edge_aadt_peak.items():
        asn = edge_vol.get(eid, 0)
        if asn > 0:
            corrections[eid] = min(max(obs / asn, 0.35), 2.75)
        elif obs > 100:
            corrections[eid] = 1.35

    zone_correction = defaultdict(list)
    for eid, corr in corrections.items():
        zid = edge_to_zone.get(eid)
        if zid:
            zone_correction[zid].append(corr)
    zone_factors = {z: statistics.median(zone_correction.get(z, [1.0])) for z in zones}

    for i in range(N):
        for j in range(N):
            if i == j:
                T[i][j] = 0.0
                continue
            T[i][j] *= math.sqrt(zone_factors.get(zones[i], 1.0) * zone_factors.get(zones[j], 1.0))
    # Don't normalize back to initial total — let corrections adjust the level.
    # The initial demand estimate may be too high (ratio > 1.0) because external
    # zone AADT estimates are inflated. Forcing normalization back to target
    # would undo the zone-factor corrections. Instead, gently pull toward the
    # observed level using the current ratio.
    current_ratio = metrics["ratio"]
    if current_ratio > 1.5:
        # Overshooting significantly — scale everything down toward observed
        dampen = 0.7 / current_ratio  # gradual correction, not full snap
        for i in range(N):
            for j in range(N):
                if i != j:
                    T[i][j] *= dampen

best = max(range(len(history)), key=lambda i: history[i]["geh5_pct"]) if history else None
matrix["T"] = [[round(T[i][j]) for j in range(N)] for i in range(N)]
matrix["calibration_history"] = history
json.dump(matrix, open("od/od_matrix_extended_calibrated.json", "w"), indent=2)

print("\n" + "=" * 70)
print("  EXTENDED CALIBRATION HISTORY")
print("=" * 70)
print(f"  {'Iter':>4}  {'GEH<5%':>7}  {'Mean GEH':>8}  {'RMSE':>6}  {'R':>6}  {'Ratio':>6}")
for i, m in enumerate(history):
    print(f"  {i + 1:>4}  {m['geh5_pct']:>6.1f}%  {m['mean_geh']:>8.1f}  {m['rmse']:>6.0f}  {m['correlation']:>6.3f}  {m['ratio']:>6.2f}")
if best is not None:
    print(f"\nBest iteration: {best + 1} (GEH<5: {history[best]['geh5_pct']:.1f}%)")

n_final, final_routes = write_final_routes(T, zones, taz_edges)
print(f"Final calibrated trips: {n_final}")
if final_routes:
    edge_vol = extract_volumes(final_routes)
    final_metrics = compute_metrics(edge_vol, edge_aadt_peak)
    json.dump(final_metrics, open("od/calibration_metrics_extended_calibrated.json", "w"), indent=2)
    print("\nFinal routed calibrated metrics:")
    print(f"  GEH < 5: {final_metrics['geh5_pct']:.1f}%")
    print(f"  Mean GEH: {final_metrics['mean_geh']:.2f}")
    print(f"  RMSE: {final_metrics['rmse']:.1f}")
    print(f"  R: {final_metrics['correlation']:.4f}")
    print(f"  Ratio: {final_metrics['ratio']:.2f}")
    print(f"  Routes: {final_routes}")
print("Calibrated matrix: od/od_matrix_extended_calibrated.json")
