#!/usr/bin/env python3
"""
Create external TAZs from passenger boundary edges.

Outputs:
  od/taz_external.add.xml
  od/external_zones.json
"""
import csv
import json
import math
import os
import sys
from collections import defaultdict

SUMO_HOME = os.environ.get("SUMO_HOME", "/usr/share/sumo")
sys.path.insert(0, os.path.join(SUMO_HOME, "tools"))
import sumolib  # noqa: E402

NET_FILE = "calgary_downtown.net.xml"
AADT_CSV = "aadt/edge_volumes.csv"
OUT_TAZ = "od/taz_external.add.xml"
OUT_JSON = "od/external_zones.json"

LAT_MIN, LAT_MAX = 51.03, 51.08
LON_MIN, LON_MAX = -114.10, -114.04
CENTER_LAT, CENTER_LON = 51.055, -114.07
BOUNDARY_TOL = 0.003
FALLBACK_TOL = 0.006
PHF = 0.09
MIN_EDGES = 3


def load_aadt():
    aadt = {}
    sections = {}
    with open(AADT_CSV, newline="") as f:
        for row in csv.DictReader(f):
            aadt[row["edge_id"]] = float(row["aadt_volume"])
            sections[row["edge_id"]] = row.get("section_name", "")
    return aadt, sections


def node_degree(node):
    return len(node.getIncoming()) + len(node.getOutgoing())


def edge_allows_passenger(edge):
    try:
        return edge.allows("passenger")
    except Exception:
        return any(lane.allows("passenger") for lane in edge.getLanes())


def edge_mid_lonlat(net, edge):
    shape = edge.getShape()
    if shape:
        x = sum(p[0] for p in shape) / len(shape)
        y = sum(p[1] for p in shape) / len(shape)
    else:
        x = (edge.getFromNode().getCoord()[0] + edge.getToNode().getCoord()[0]) / 2
        y = (edge.getFromNode().getCoord()[1] + edge.getToNode().getCoord()[1]) / 2
    lon, lat = net.convertXY2LonLat(x, y)
    return lon, lat


def boundary_distance(lat, lon):
    return min(abs(lat - LAT_MIN), abs(lat - LAT_MAX), abs(lon - LON_MIN), abs(lon - LON_MAX))


def sector_for(lat, lon):
    angle = math.degrees(math.atan2(lon - CENTER_LON, lat - CENTER_LAT))
    octants = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return octants[int((angle + 22.5) % 360 // 45)]


def cardinal_sector(sector):
    if sector in {"NE", "NW"}:
        return "N"
    if sector in {"SE", "SW"}:
        return "S"
    return sector


def nearest_aadt_estimate(lat, lon, measured_edges):
    measured = []
    for rec in measured_edges:
        d = math.hypot((lat - rec["lat"]) * 111.0, (lon - rec["lon"]) * 70.0)
        measured.append((d, rec["aadt"]))
    if not measured:
        return 0.0
    measured.sort(key=lambda x: x[0])
    return sum(v for _, v in measured[:10])


print("Loading AADT observations...")
edge_aadt, edge_sections = load_aadt()
print(f"  AADT edges: {len(edge_aadt)}")

print("Loading network...")
net = sumolib.net.readNet(NET_FILE)

boundary = []
measured_edges = []
nearby_by_sector = defaultdict(list)
for edge in net.getEdges():
    eid = edge.getID()
    if ":" in eid or not edge_allows_passenger(edge):
        continue
    try:
        lon, lat = edge_mid_lonlat(net, edge)
    except Exception:
        continue
    dist = boundary_distance(lat, lon)
    sector = sector_for(lat, lon)
    low_degree = min(node_degree(edge.getFromNode()), node_degree(edge.getToNode())) <= 2
    rec = {"id": eid, "lat": lat, "lon": lon, "sector": sector, "dist": dist}
    if eid in edge_aadt:
        measured_edges.append({"id": eid, "lat": lat, "lon": lon, "aadt": edge_aadt[eid]})
    if dist <= FALLBACK_TOL:
        nearby_by_sector[sector].append(rec)
    if dist <= BOUNDARY_TOL and low_degree:
        boundary.append(rec)

sector_edges = defaultdict(list)
for rec in boundary:
    sector_edges[cardinal_sector(rec["sector"])].append(rec)

zones = {}
for sector in ["N", "E", "S", "W"]:
    edges = sector_edges.get(sector, [])
    if len(edges) < MIN_EDGES:
        continue
    measured_aadt = sum(edge_aadt.get(e["id"], 0.0) for e in edges)
    total_aadt = measured_aadt
    estimate_method = "boundary_aadt"
    if total_aadt <= 0:
        clat = sum(e["lat"] for e in edges) / len(edges)
        clon = sum(e["lon"] for e in edges) / len(edges)
        total_aadt = nearest_aadt_estimate(clat, clon, measured_edges)
        estimate_method = "nearest_aadt"
    if total_aadt <= 0:
        continue

    # Keep routing choices bounded and prioritize measured/high-volume edges.
    edges_sorted = sorted(edges, key=lambda e: (edge_aadt.get(e["id"], 0.0), -e["dist"]), reverse=True)
    selected = edges_sorted[:25]
    zid = f"ext_{sector}"
    production = total_aadt * PHF * 0.5
    attraction = total_aadt * PHF * 0.5
    zones[zid] = {
        "name": zid,
        "sector": sector,
        "centroid_lat": round(sum(e["lat"] for e in selected) / len(selected), 6),
        "centroid_lon": round(sum(e["lon"] for e in selected) / len(selected), 6),
        "production": round(production),
        "attraction": round(attraction),
        "total_aadt": round(total_aadt, 1),
        "measured_boundary_aadt": round(measured_aadt, 1),
        "estimate_method": estimate_method,
        "boundary_edges": [e["id"] for e in selected],
        "n_boundary_edges": len(edges),
    }

print(f"Boundary passenger edges found: {len(boundary)}")
print(f"External zones created: {len(zones)}")
for zid, z in zones.items():
    print(f"  {zid}: {z['n_boundary_edges']} boundary edges, AADT={z['total_aadt']:.0f}, P=A={z['production']}")

with open(OUT_TAZ, "w") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    f.write("<additional>\n")
    for zid in sorted(zones):
        f.write(f'  <taz id="{zid}">\n')
        for eid in zones[zid]["boundary_edges"]:
            weight = max(edge_aadt.get(eid, 1.0), 1.0)
            f.write(f'    <tazSource id="{eid}" weight="{weight:.1f}"/>\n')
            f.write(f'    <tazSink id="{eid}" weight="{weight:.1f}"/>\n')
        f.write("  </taz>\n")
    f.write("</additional>\n")

json.dump(zones, open(OUT_JSON, "w"), indent=2)
print(f"Wrote {OUT_TAZ}")
print(f"Wrote {OUT_JSON}")
