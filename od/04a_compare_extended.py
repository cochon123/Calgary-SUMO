#!/usr/bin/env python3
"""Compare extended routed volumes against AADT observations."""
import csv
import json
import math
import statistics
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict

ROUTES_FILE = sys.argv[1] if len(sys.argv) > 1 else "od/calgary_od_extended.rou.xml"
OUT_CSV = "od/calibration_comparison_extended.csv"
OUT_JSON = "od/calibration_metrics_extended.json"
PHF = 0.09


def geh(obs, asn):
    if obs + asn < 1e-10:
        return 0.0
    return math.sqrt(2 * (obs - asn) ** 2 / (obs + asn))


def load_aadt():
    data = {}
    sections = {}
    with open("aadt/edge_volumes.csv", newline="") as f:
        for row in csv.DictReader(f):
            data[row["edge_id"]] = float(row["aadt_volume"]) * PHF
            sections[row["edge_id"]] = row.get("section_name", "")
    return data, sections


def extract_volumes(routes_file):
    edge_volume = defaultdict(int)
    n_routes = 0
    context = ET.iterparse(routes_file, events=("end",))
    for _, elem in context:
        if elem.tag == "vehicle":
            n_routes += 1
            route = elem.find("route")
            if route is not None and route.get("edges"):
                for eid in route.get("edges").split():
                    if ":" not in eid:
                        edge_volume[eid] += 1
            elem.clear()
    return edge_volume, n_routes


def compute_metrics(edge_volume):
    edge_aadt_peak, sections = load_aadt()
    rows = []
    for eid, obs in edge_aadt_peak.items():
        asn = edge_volume.get(eid, 0)
        rows.append({"edge": eid, "section": sections.get(eid, ""), "observed": obs, "assigned": asn, "geh": geh(obs, asn)})
    gehs = [r["geh"] for r in rows]
    obs = [r["observed"] for r in rows]
    asn = [r["assigned"] for r in rows]
    mean_obs = statistics.mean(obs)
    mean_asn = statistics.mean(asn)
    denom = math.sqrt(sum((o - mean_obs) ** 2 for o in obs) * sum((a - mean_asn) ** 2 for a in asn))
    corr = sum((o - mean_obs) * (a - mean_asn) for o, a in zip(obs, asn)) / denom if denom else 0.0
    metrics = {
        "edges_compared": len(rows),
        "mean_observed": round(mean_obs, 1),
        "mean_assigned": round(mean_asn, 1),
        "ratio": round(mean_asn / mean_obs, 3) if mean_obs else 0,
        "geh_under_5_pct": round(100 * sum(1 for g in gehs if g < 5) / len(gehs), 1),
        "geh_under_10_pct": round(100 * sum(1 for g in gehs if g < 10) / len(gehs), 1),
        "mean_geh": round(statistics.mean(gehs), 2),
        "rmse": round(math.sqrt(sum((o - a) ** 2 for o, a in zip(obs, asn)) / len(obs)), 1),
        "correlation": round(corr, 4),
    }
    return rows, metrics


edge_volume, n_routes = extract_volumes(ROUTES_FILE)
rows, metrics = compute_metrics(edge_volume)

print("=" * 70)
print("  EXTENDED CALIBRATION COMPARISON")
print("=" * 70)
print(f"Routes parsed: {n_routes}")
print(f"Edges with assigned volume: {len(edge_volume)}")
print(f"Edges compared: {metrics['edges_compared']}")
print(f"GEH < 5: {metrics['geh_under_5_pct']:.1f}%")
print(f"Mean GEH: {metrics['mean_geh']:.2f}")
print(f"RMSE: {metrics['rmse']:.1f}")
print(f"R (Pearson): {metrics['correlation']:.4f}")
print(f"Ratio assigned/observed: {metrics['ratio']:.2f}")

print("\nMemorial / Macleod checks:")
for r in sorted(rows, key=lambda x: x["section"]):
    section = r["section"].upper()
    if "MEMOR" in section or "MACLEOD" in section or "MAC" in section:
        print(f"  {r['section'][:14]:14s} GEH={r['geh']:6.1f} obs={r['observed']:6.0f} asn={r['assigned']:5d} {r['edge'][:24]}")

print("\nWorst 10 edges:")
for r in sorted(rows, key=lambda x: x["geh"], reverse=True)[:10]:
    print(f"  GEH={r['geh']:6.1f} obs={r['observed']:6.0f} asn={r['assigned']:5d} {r['section'][:12]:12s} {r['edge'][:24]}")

with open(OUT_CSV, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["edge_id", "section_name", "observed_peak_hour", "assigned_volume", "geh"])
    for r in rows:
        w.writerow([r["edge"], r["section"], f"{r['observed']:.1f}", r["assigned"], f"{r['geh']:.2f}"])
json.dump(metrics, open(OUT_JSON, "w"), indent=2)
print(f"\nWrote {OUT_CSV}")
print(f"Wrote {OUT_JSON}")
