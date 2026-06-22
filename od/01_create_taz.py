#!/usr/bin/env python3
"""
Step 1: Define Traffic Analysis Zones (TAZs) for downtown Calgary.

Divides the downtown bbox (51.03-51.08N, 114.04-114.10W) into a 4x3 grid
of 12 zones, assigns each non-internal edge to its zone, and estimates
zone-level productions and attractions from AADT data.

Key correction vs. previous approach:
  AADT measures FLOW on a link, not trips generated. A single vehicle
  traverses ~5 AADT measurement points on average. So zone productions
  ≈ sum(AADT_in_zone) * PHF / avg_edges_per_trip, NOT sum(AADT) * PHF.

Outputs:
  - od/taz.add.xml           TAZ definitions (sources/sinks per zone)
  - od/zone_stats.json       zone centroids, productions, attractions
"""
import json, csv, math, sys, os, re
from collections import defaultdict

SUMO_HOME = os.environ.get('SUMO_HOME', '/usr/share/sumo')
sys.path.insert(0, os.path.join(SUMO_HOME, 'tools'))
import sumolib

NET_FILE = 'calgary_downtown.net.xml'
AADT_CSV = 'aadt/edge_volumes.csv'

# Grid: 4 columns x 3 rows = 12 zones
GRID_COLS = 4
GRID_ROWS = 3

# Downtown bbox
LAT_MIN, LAT_MAX = 51.03, 51.08
LON_MIN, LON_MAX = -114.10, -114.04

# Peak hour factor: ADWT -> peak hour vehicles
PHF = 0.09

# Average number of AADT measurement points a trip traverses.
# This converts from link-volume counts to trip-level demand.
# Estimated: downtown Calgary avg trip ~2-3km, AADT points ~every 500m → ~5 crossings.
AVG_CROSSINGS = 5.0

print("Loading network...")
net = sumolib.net.readNet(NET_FILE)

# --- Build AADT edge -> volume mapping ---------------------------------------
edge_aadt = {}
with open(AADT_CSV) as f:
    for row in csv.DictReader(f):
        edge_aadt[row['edge_id']] = float(row['aadt_volume'])
print(f"AADT-matched edges: {len(edge_aadt)}")

# --- Assign each edge to a grid zone ----------------------------------------
print("\nAssigning edges to zones...")
zone_edges = defaultdict(list)  # zone_id -> [edge_ids]
zone_aadt_sum = defaultdict(float)  # zone_id -> sum of AADT
zone_centroids = defaultdict(lambda: {'lat_sum': 0, 'lon_sum': 0, 'count': 0})

def latlon_to_zone(lat, lon):
    """Map lat/lon to grid zone index."""
    col = int((lon - LON_MIN) / (LON_MAX - LON_MIN) * GRID_COLS)
    row = int((lat - LAT_MIN) / (LAT_MAX - LAT_MIN) * GRID_ROWS)
    col = max(0, min(GRID_COLS - 1, col))
    row = max(0, min(GRID_ROWS - 1, row))
    return f"taz_{row}_{col}"

for edge in net.getEdges():
    eid = edge.getID()
    if ':' in eid:
        continue  # skip internal edges

    # Get edge centroid in lat/lon
    from_j = edge.getFromNode()
    to_j = edge.getToNode()
    x_mid = (from_j.getCoord()[0] + to_j.getCoord()[0]) / 2
    y_mid = (from_j.getCoord()[1] + to_j.getCoord()[1]) / 2

    try:
        lon, lat = net.convertXY2LonLat(x_mid, y_mid)
    except Exception:
        continue

    zone_id = latlon_to_zone(lat, lon)
    zone_edges[zone_id].append(eid)
    zone_centroids[zone_id]['lat_sum'] += lat
    zone_centroids[zone_id]['lon_sum'] += lon
    zone_centroids[zone_id]['count'] += 1

    if eid in edge_aadt:
        zone_aadt_sum[zone_id] += edge_aadt[eid]

# --- Compute productions and attractions per zone ----------------------------
zones = sorted(zone_edges.keys())
print(f"\nZones created: {len(zones)}")

zone_data = {}
total_trips = 0
for zid in zones:
    n_edges = len(zone_edges[zid])
    aadt_sum = zone_aadt_sum.get(zid, 0)
    # Zone production: daily link volume * peak hour factor / avg crossings
    production = aadt_sum * PHF / AVG_CROSSINGS
    attraction = production  # symmetric (no external data to differentiate)
    total_trips += production

    c = zone_centroids[zid]
    clat = c['lat_sum'] / c['count'] if c['count'] > 0 else 0
    clon = c['lon_sum'] / c['count'] if c['count'] > 0 else 0

    zone_data[zid] = {
        'n_edges': n_edges,
        'aadt_sum': aadt_sum,
        'production': round(production),
        'attraction': round(attraction),
        'centroid_lat': round(clat, 6),
        'centroid_lon': round(clon, 6),
    }
    print(f"  {zid}: {n_edges:>5} edges, AADT sum={aadt_sum:>8.0f}, "
          f"P={production:>6.0f} veh/h")

print(f"\nTotal estimated peak-hour trips: {total_trips:.0f}")
print(f"(vs. previous erroneous ~201,600 — corrected by /{AVG_CROSSINGS})")

# --- Write TAZ file ----------------------------------------------------------
print("\nWriting taz.add.xml...")
with open('od/taz.add.xml', 'w') as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    f.write('<additional>\n')
    for zid in zones:
        # Limit to edges that allow passenger vehicles
        valid = []
        for eid in zone_edges[zid]:
            edge = net.getEdge(eid)
            if edge and any(lane.allows('passenger') for lane in edge.getLanes()):
                valid.append(eid)
        # If zone has no passenger edges, use all as fallback
        if not valid:
            valid = zone_edges[zid][:10]

        # Use fringe edges (edges connecting to boundary nodes) as preferred sources/sinks
        f.write(f'  <taz id="{zid}">\n')
        for eid in valid:
            f.write(f'    <tazSource id="{eid}" weight="1"/>\n')
            f.write(f'    <tazSink id="{eid}" weight="1"/>\n')
        f.write('  </taz>\n')
    f.write('</additional>\n')
print(f"  {len(zones)} TAZs written to od/taz.add.xml")

# --- Save zone stats ---------------------------------------------------------
os.makedirs('od', exist_ok=True)
json.dump(zone_data, open('od/zone_stats.json', 'w'), indent=2)
print(f"  Zone stats saved to od/zone_stats.json")
