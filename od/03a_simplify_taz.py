#!/usr/bin/env python3
"""
Create simplified TAZs without loading the SUMO network.
Parses the existing taz.add.xml and keeps only the top edges per zone
(ranked by AADT volume). No sumolib needed — pure Python.
"""
import xml.etree.ElementTree as ET
import csv, json

# Load AADT edge mapping
edge_aadt = {}
with open('aadt/edge_volumes.csv') as f:
    for row in csv.DictReader(f):
        edge_aadt[row['edge_id']] = float(row['aadt_volume'])

# Parse existing TAZ file
tree = ET.parse('od/taz.add.xml')
zones = json.load(open('od/zone_stats.json'))

N_PER_ZONE = 15
total_before = 0
total_after = 0

with open('od/taz_simple.add.xml', 'w') as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    f.write('<additional>\n')
    for taz in tree.findall('.//taz'):
        zid = taz.get('id')
        edges = [s.get('id') for s in taz.findall('tazSource')]
        total_before += len(edges)

        # Sort: AADT edges first (by volume desc), then others
        edges_sorted = sorted(edges, key=lambda e: edge_aadt.get(e, 0), reverse=True)
        selected = edges_sorted[:N_PER_ZONE]
        total_after += len(selected)

        f.write(f'  <taz id="{zid}">\n')
        for eid in selected:
            weight = str(int(edge_aadt.get(eid, 1)))  # weight by AADT
            f.write(f'    <tazSource id="{eid}" weight="{weight}"/>\n')
            f.write(f'    <tazSink id="{eid}" weight="{weight}"/>\n')
        f.write('  </taz>\n')
    f.write('</additional>\n')

print(f"Edges: {total_before} -> {total_after} (across {len(zones)} zones)")
print(f"Written to od/taz_simple.add.xml")
