#!/usr/bin/env python3
"""
Match AADT measurement midpoints to nearest SUMO edges using sumolib.
Outputs aadt/edge_volumes.csv: edge_id, aadt_volume, section_name
"""
import json, sys, os, csv

SUMO_HOME = os.environ.get('SUMO_HOME', '/usr/share/sumo')
sys.path.insert(0, os.path.join(SUMO_HOME, 'tools'))
import sumolib

NET_FILE = 'calgary_downtown.net.xml'
AADT_FILE = 'aadt/downtown_aadt.json'
OUT_CSV = 'aadt/edge_volumes.csv'

print("Loading network (this takes a moment on a 75MB network)...")
net = sumolib.net.readNet(NET_FILE)
print("Network loaded.")

# Determine projection: check if network uses geo-coordinates
# sumolib can convert lon/lat -> x/y if the network has a projection
print("Network offset:", net.getLocationOffset())
print("Has projection:", net._geoReferenceProjector is not None if hasattr(net, '_geoReferenceProjector') else 'unknown')

aadt = json.load(open(AADT_FILE))
print(f"AADT records to match: {len(aadt)}")

RADIUS = 50  # meters — search radius for nearest edge
matched = []
unmatched = 0

for i, rec in enumerate(aadt):
    lat, lon = rec['midlat'], rec['midlon']
    vol = rec['volume']
    name = rec['section_name']

    # Convert lon/lat to network x/y
    try:
        x, y = net.convertLonLat2XY(lon, lat)
    except Exception as e:
        # Fallback: try getGeoConv
        try:
            x, y = net.convertLonLat2XY(lon, lat)
        except:
            print(f"  [{i}] {name}: coord conversion failed: {e}")
            unmatched += 1
            continue

    # Find nearest edges within radius
    edges = net.getNeighboringEdges(x, y, RADIUS)
    if not edges:
        # Try larger radius
        edges = net.getNeighboringEdges(x, y, 200)
    if not edges:
        unmatched += 1
        continue

    # Sort by distance, pick closest non-internal edge
    edges.sort(key=lambda e: e[1])
    best_edge = None
    for edge, dist in edges:
        eid = edge.getID()
        if ':' not in eid:  # skip internal edges
            best_edge = eid
            break
    if best_edge is None:
        best_edge = edges[0][0].getID()

    matched.append({
        'edge_id': best_edge,
        'aadt_volume': vol,
        'section_name': name,
        'midlat': lat,
        'midlon': lon,
    })

print(f"\nMatched: {len(matched)} / Unmatched: {unmatched}")

# Aggregate: if multiple AADT points map to same edge, take the max volume
from collections import defaultdict
edge_max_vol = defaultdict(lambda: {'vol': 0, 'name': ''})
for m in matched:
    eid = m['edge_id']
    if m['aadt_volume'] > edge_max_vol[eid]['vol']:
        edge_max_vol[eid] = {'vol': m['aadt_volume'], 'name': m['section_name']}

# Write CSV
with open(OUT_CSV, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['edge_id', 'aadt_volume', 'section_name'])
    for eid, info in sorted(edge_max_vol.items(), key=lambda x: -x[1]['vol']):
        w.writerow([eid, info['vol'], info['name']])

print(f"Unique calibrated edges: {len(edge_max_vol)}")
print(f"Written to {OUT_CSV}")
print("\nTop 10 highest-volume edges:")
for eid, info in list(sorted(edge_max_vol.items(), key=lambda x: -x[1]['vol']))[:10]:
    print(f"  {info['vol']:>7.0f}  {eid:<30}  {info['name']}")
