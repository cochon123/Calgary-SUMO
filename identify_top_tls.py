#!/usr/bin/env python3
"""Identify high-load traffic lights and select a downtown corridor."""
import json
import math
import os
import sys
from itertools import combinations

SUMO_HOME = os.environ.get("SUMO_HOME", "/usr/share/sumo")
sys.path.insert(0, os.path.join(SUMO_HOME, "tools"))
import sumolib

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
NET_FILE = os.path.join(PROJECT_DIR, "calgary_downtown.net.xml")
EDGES_GEOJSON = os.path.join(PROJECT_DIR, "viz", "edges.geojson")
TLS_POINTS_GEOJSON = os.path.join(PROJECT_DIR, "viz", "tls_points.geojson")
OUTPUT_FILE = os.path.join(PROJECT_DIR, "output", "top_tls.json")

EARTH_RADIUS_M = 6371000.0


def load_geojson(path):
    with open(path) as f:
        return json.load(f)


def haversine_m(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    x = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(x))


def project_local(points):
    lat0 = math.radians(sum(p[1] for p in points) / len(points))
    lon0 = math.radians(sum(p[0] for p in points) / len(points))
    projected = []
    for lon, lat in points:
        x = (math.radians(lon) - lon0) * math.cos(lat0) * EARTH_RADIUS_M
        y = (math.radians(lat) - math.radians(sum(p[1] for p in points) / len(points))) * EARTH_RADIUS_M
        projected.append((x, y))
    return projected


def line_distance(point, a, b):
    px, py = point
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    denom = math.hypot(dx, dy)
    if denom == 0:
        return math.hypot(px - ax, py - ay)
    return abs(dy * px - dx * py + bx * ay - by * ax) / denom


def line_position(point, a, b):
    px, py = point
    ax, ay = a
    bx, by = b
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom == 0:
        return 0.0
    return ((px - ax) * dx + (py - ay) * dy) / math.sqrt(denom)


def bearing_degrees(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon = lon2 - lon1
    y = math.sin(dlon) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def direction_name(bearing):
    bearing = bearing % 180
    if bearing <= 22.5 or bearing >= 157.5:
        return "north-south arterial"
    if 67.5 <= bearing <= 112.5:
        return "east-west arterial"
    if bearing < 67.5:
        return "northeast-southwest arterial"
    return "northwest-southeast arterial"


def score_tls_load(net, edge_volumes, tls_points):
    tls_by_id = {tls.getID(): tls for tls in net.getTrafficLights()}
    rows = []
    for tls_id, coord in tls_points.items():
        tls = tls_by_id.get(tls_id)
        if tls is None:
            continue
        edge_ids = set()
        for conn in tls.getConnections():
            for lane in conn[:2]:
                if lane is not None:
                    edge_ids.add(lane.getEdge().getID())
        controlled_edges = [
            {
                "id": edge_id,
                "volume": edge_volumes.get(edge_id, 0),
            }
            for edge_id in sorted(edge_ids)
        ]
        rows.append(
            {
                "id": tls_id,
                "load_score": sum(e["volume"] for e in controlled_edges),
                "coordinates": coord,
                "n_edges": len(controlled_edges),
                "controlled_edges": controlled_edges,
            }
        )
    return sorted(rows, key=lambda r: r["load_score"], reverse=True)


def identify_corridor(candidates):
    usable = [c for c in candidates if c["load_score"] > 0]
    coords = [c["coordinates"] for c in usable]
    projected = project_local(coords)
    best = None
    for i, j in combinations(range(len(usable)), 2):
        if haversine_m(coords[i], coords[j]) < 450:
            continue
        members = []
        for k, point in enumerate(projected):
            distance = line_distance(point, projected[i], projected[j])
            if distance <= 85:
                pos = line_position(point, projected[i], projected[j])
                members.append((k, pos, distance))
        if len(members) < 5:
            continue
        members.sort(key=lambda x: x[1])
        trimmed = members[:10]
        span = haversine_m(coords[trimmed[0][0]], coords[trimmed[-1][0]])
        load = sum(usable[k]["load_score"] for k, _, _ in trimmed)
        score = len(trimmed) * 1_000_000 + load + span
        if best is None or score > best["score"]:
            best = {"score": score, "members": trimmed, "endpoints": (i, j)}
    if best is None:
        raise RuntimeError("Could not identify a 5+ TLS aligned corridor from top candidates")

    ordered = []
    for order, (idx, _, offset_m) in enumerate(best["members"], start=1):
        item = dict(usable[idx])
        item["order"] = order
        item["line_offset_m"] = round(offset_m, 1)
        ordered.append(item)

    if ordered[0]["coordinates"][1] > ordered[-1]["coordinates"][1]:
        ordered.reverse()
        for order, item in enumerate(ordered, start=1):
            item["order"] = order

    for prev, cur in zip(ordered, ordered[1:]):
        cur["distance_from_previous_m"] = round(haversine_m(prev["coordinates"], cur["coordinates"]), 1)
    ordered[0]["distance_from_previous_m"] = 0.0

    bearing = bearing_degrees(ordered[0]["coordinates"], ordered[-1]["coordinates"])
    return {
        "description": direction_name(bearing),
        "bearing_degrees": round(bearing, 1),
        "tls_ids": [c["id"] for c in ordered],
        "members": ordered,
    }


def main():
    os.makedirs(os.path.join(PROJECT_DIR, "output"), exist_ok=True)
    edge_features = load_geojson(EDGES_GEOJSON)["features"]
    tls_features = load_geojson(TLS_POINTS_GEOJSON)["features"]
    edge_volumes = {f["properties"]["id"]: int(f["properties"].get("volume", 0) or 0) for f in edge_features}
    tls_points = {f["properties"]["id"]: f["geometry"]["coordinates"] for f in tls_features}

    print("Loading SUMO network...")
    net = sumolib.net.readNet(NET_FILE)
    ranked = score_tls_load(net, edge_volumes, tls_points)
    top20 = ranked[:20]
    corridor = identify_corridor(top20)

    with open(OUTPUT_FILE, "w") as f:
        json.dump({"top20": top20, "corridor": corridor}, f, indent=2)

    print("\nTop 20 loaded TLS")
    print(f"{'Rank':>4} {'Load':>8} {'Edges':>5}  {'Lon':>11} {'Lat':>10}  TLS")
    for rank, row in enumerate(top20, start=1):
        lon, lat = row["coordinates"]
        print(f"{rank:>4} {row['load_score']:>8.0f} {row['n_edges']:>5}  {lon:>11.6f} {lat:>10.6f}  {row['id']}")

    print("\nIdentified corridor")
    print(f"Road guess: {corridor['description']} | bearing {corridor['bearing_degrees']:.1f} deg")
    print(f"{'Ord':>3} {'Load':>8} {'Dist m':>8} {'Lon':>11} {'Lat':>10}  TLS")
    for row in corridor["members"]:
        lon, lat = row["coordinates"]
        print(
            f"{row['order']:>3} {row['load_score']:>8.0f} {row['distance_from_previous_m']:>8.1f} "
            f"{lon:>11.6f} {lat:>10.6f}  {row['id']}"
        )
    print("\nCorridor TLS IDs JSON:")
    print(json.dumps(corridor["tls_ids"], indent=2))
    print(f"\nWrote {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
