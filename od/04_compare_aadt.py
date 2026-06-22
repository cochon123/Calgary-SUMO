#!/usr/bin/env python3
"""
Step 4: Extract edge volumes from routed trips and compare to AADT.

Parses the duarouter output (routes) to count how many vehicles traverse
each edge. This is the assigned traffic volume — the output of the
traffic assignment step. Compares to observed AADT for calibration.

Metrics:
  - GEH (Geoffrey E. Havers) statistic: standard calibration metric
    GEH < 5 for 85%+ of links is the industry standard for "good fit"
  - RMSE (Root Mean Square Error)
  - Correlation coefficient
"""
import xml.etree.ElementTree as ET
import json, csv, math
from collections import defaultdict

# --- Parse routes and count edge volumes -------------------------------------
print("Parsing routes and counting edge volumes...")
edge_volume = defaultdict(int)
n_routes = 0
n_failed = 0

# Routes are in <vehicle> elements with nested <route> elements
# Some may have route references, others have inline routes
context = ET.iterparse('od/calgary_od.rou.xml', events=('end',))
for event, elem in context:
    if elem.tag == 'vehicle':
        n_routes += 1
        route = elem.find('route')
        if route is not None and route.get('edges'):
            edges = route.get('edges').split()
            for eid in edges:
                if ':' not in eid:  # skip internal edges
                    edge_volume[eid] += 1
        else:
            n_failed += 1
        elem.clear()  # free memory

print(f"Routes parsed: {n_routes} (failed: {n_failed})")
print(f"Edges with volume > 0: {len(edge_volume)}")
total_assigned = sum(edge_volume.values())
print(f"Total edge-volume sum: {total_assigned} (avg {total_assigned/max(1,len(edge_volume)):.1f} veh/edge)")

# --- Load AADT observations --------------------------------------------------
edge_aadt = {}
with open('aadt/edge_volumes.csv') as f:
    for row in csv.DictReader(f):
        edge_aadt[row['edge_id']] = float(row['aadt_volume'])

# AADT is daily volume; our assignment is peak-hour volume
# Convert AADT to peak-hour equivalent for comparison
PHF = 0.09  # peak hour factor
edge_aadt_peak = {e: v * PHF for e, v in edge_aadt.items()}

# --- Compare -----------------------------------------------------------------
print(f"\n{'='*70}")
print(f"  CALIBRATION COMPARISON: Assigned vs Observed (AADT * PHF)")
print(f"{'='*70}")

# For each AADT-matched edge, compare assigned volume to observed
comparisons = []
for eid, observed_peak in edge_aadt_peak.items():
    assigned = edge_volume.get(eid, 0)
    comparisons.append({
        'edge': eid,
        'observed_peak': observed_peak,
        'assigned': assigned,
    })

# --- GEH statistic -----------------------------------------------------------
def geh(observed, assigned):
    """GEH = sqrt(2*(M-C)^2 / (M+C))"""
    m, c = observed, assigned
    denom = (m + c) / 2.0
    if denom < 1e-10:
        return 0
    return math.sqrt(2 * (m - c)**2 / (m + c))

geh_values = [geh(c['observed_peak'], c['assigned']) for c in comparisons]
geh_under5 = sum(1 for g in geh_values if g < 5)
geh_under10 = sum(1 for g in geh_values if g < 10)

# --- RMSE --------------------------------------------------------------------
errors = [(c['observed_peak'] - c['assigned']) for c in comparisons]
rmse = math.sqrt(sum(e**2 for e in errors) / len(errors))

# --- Correlation -------------------------------------------------------------
import statistics
obs = [c['observed_peak'] for c in comparisons]
asn = [c['assigned'] for c in comparisons]
mean_obs = statistics.mean(obs)
mean_asn = statistics.mean(asn)
numerator = sum((o - mean_obs) * (a - mean_asn) for o, a in zip(obs, asn))
denom1 = math.sqrt(sum((o - mean_obs)**2 for o in obs))
denom2 = math.sqrt(sum((a - mean_asn)**2 for a in asn))
correlation = numerator / (denom1 * denom2) if denom1 * denom2 > 0 else 0

# --- Report ------------------------------------------------------------------
print(f"\n  Edges compared:     {len(comparisons)}")
print(f"  Mean observed (peak hr):  {mean_obs:>8.1f} veh")
print(f"  Mean assigned:            {mean_asn:>8.1f} veh")
print(f"  Ratio assigned/observed:  {mean_asn/mean_obs:>8.2f}")
print(f"")
print(f"  GEH < 5:   {geh_under5:>3} / {len(comparisons)} "
      f"({100*geh_under5/len(comparisons):.1f}%) — industry target: >85%")
print(f"  GEH < 10:  {geh_under10:>3} / {len(comparisons)} "
      f"({100*geh_under10/len(comparisons):.1f}%)")
print(f"  Mean GEH:  {statistics.mean(geh_values):.2f}")
print(f"")
print(f"  RMSE:      {rmse:.1f} veh")
print(f"  R (Pearson): {correlation:.4f}")

# --- Worst-fitting edges -----------------------------------------------------
print(f"\n  Worst 10 edges (highest GEH):")
for c, g in sorted(zip(comparisons, geh_values), key=lambda x: x[1], reverse=True)[:10]:
    eid = c['edge']
    eid_short = eid[:25]
    print(f"    GEH={g:>7.1f}  obs={c['observed_peak']:>6.0f}  asn={c['assigned']:>5d}  {eid_short}")

# --- Best-fitting edges ------------------------------------------------------
print(f"\n  Best 10 edges (lowest GEH):")
for c, g in sorted(zip(comparisons, geh_values), key=lambda x: x[1])[:10]:
    eid = c['edge']
    eid_short = eid[:25]
    print(f"    GEH={g:>7.1f}  obs={c['observed_peak']:>6.0f}  asn={c['assigned']:>5d}  {eid_short}")

# --- Save comparison data ----------------------------------------------------
with open('od/calibration_comparison.csv', 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['edge_id', 'observed_peak_hour', 'assigned_volume', 'geh'])
    for c, g in zip(comparisons, geh_values):
        w.writerow([c['edge'], f"{c['observed_peak']:.1f}", c['assigned'], f"{g:.2f}"])
print(f"\n  Full comparison: od/calibration_comparison.csv")

# Save summary metrics
metrics = {
    'edges_compared': len(comparisons),
    'mean_observed': round(mean_obs, 1),
    'mean_assigned': round(mean_asn, 1),
    'ratio': round(mean_asn / mean_obs, 3),
    'geh_under_5_pct': round(100 * geh_under5 / len(comparisons), 1),
    'geh_under_10_pct': round(100 * geh_under10 / len(comparisons), 1),
    'mean_geh': round(statistics.mean(geh_values), 2),
    'rmse': round(rmse, 1),
    'correlation': round(correlation, 4),
}
json.dump(metrics, open('od/calibration_metrics.json', 'w'), indent=2)
print(f"  Metrics: od/calibration_metrics.json")
