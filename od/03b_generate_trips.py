#!/usr/bin/env python3
"""
Step 3b: Generate trips directly from OD matrix + simplified TAZs.

Bypasses od2trips entirely. For each OD pair, picks weighted-random
source and sink edges from the TAZ definitions.

Output: od/od_trips.xml (SUMO trip format with from/to edges)
"""
import xml.etree.ElementTree as ET
import json, random, csv

random.seed(42)

# Load OD matrix
matrix = json.load(open('od/od_matrix.json'))
zones = matrix['zones']
T = matrix['T']
N = len(zones)

# Load simplified TAZ edges (with weights)
taz_edges = {}  # zone_id -> [(edge_id, weight)]
tree = ET.parse('od/taz_simple.add.xml')
for taz in tree.findall('.//taz'):
    zid = taz.get('id')
    sources = [(s.get('id'), float(s.get('weight', 1)))
               for s in taz.findall('tazSource')]
    if sources:
        taz_edges[zid] = sources

print(f"Zones with edges: {len(taz_edges)}")
for zid in sorted(taz_edges.keys()):
    print(f"  {zid}: {len(taz_edges[zid])} source/sink edges")

def weighted_choice(items):
    """Pick a random item weighted by its weight."""
    total = sum(w for _, w in items)
    r = random.uniform(0, total)
    cum = 0
    for eid, w in items:
        cum += w
        if r <= cum:
            return eid
    return items[-1][0]  # fallback

# Generate trips
trip_id = 0
total_trips = 0
SIM_END = 3600

with open('od/od_trips.xml', 'w') as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    f.write('<routes>\n')

    for i in range(N):
        for j in range(N):
            if i == j:
                continue
            n_trips = int(round(T[i][j]))
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
                total_trips += 1

    f.write('</routes>\n')

print(f"\nTotal trips generated: {total_trips}")
print(f"Written to od/od_trips.xml")
