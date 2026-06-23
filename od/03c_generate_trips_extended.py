#!/usr/bin/env python3
"""Generate SUMO trips from the extended OD matrix and internal/external TAZs."""
import json
import random
import xml.etree.ElementTree as ET

MATRIX_FILE = "od/od_matrix_extended.json"
TAZ_FILES = ["od/taz_simple.add.xml", "od/taz_external.add.xml"]
OUT_FILE = "od/od_trips_extended.xml"
SIM_END = 3600
random.seed(42)


def load_taz_edges(files):
    taz_edges = {}
    for path in files:
        tree = ET.parse(path)
        for taz in tree.findall(".//taz"):
            zid = taz.get("id")
            sources = [(s.get("id"), float(s.get("weight", 1))) for s in taz.findall("tazSource")]
            if sources:
                taz_edges[zid] = sources
    return taz_edges


def weighted_choice(items):
    total = sum(w for _, w in items)
    r = random.uniform(0, total)
    acc = 0.0
    for eid, weight in items:
        acc += weight
        if r <= acc:
            return eid
    return items[-1][0]


matrix = json.load(open(MATRIX_FILE))
zones = matrix["zones"]
T = matrix["T"]
taz_edges = load_taz_edges(TAZ_FILES)

trip_id = 0
skipped = 0
with open(OUT_FILE, "w") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n<routes>\n')
    for i, origin in enumerate(zones):
        for j, dest in enumerate(zones):
            if i == j:
                continue
            n = int(round(T[i][j]))
            if n < 1:
                continue
            if origin not in taz_edges or dest not in taz_edges:
                skipped += n
                continue
            for _ in range(n):
                src = weighted_choice(taz_edges[origin])
                dst = weighted_choice(taz_edges[dest])
                if src == dst:
                    continue
                depart = random.uniform(0, SIM_END)
                f.write(f'  <trip id="t{trip_id}" depart="{depart:.1f}" from="{src}" to="{dst}"/>\n')
                trip_id += 1
    f.write("</routes>\n")

print(f"Zones with TAZ edges: {len(taz_edges)}")
print(f"Total trips generated: {trip_id}")
if skipped:
    print(f"Skipped trips with missing TAZ edges: {skipped}")
print(f"Wrote {OUT_FILE}")
