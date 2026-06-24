#!/usr/bin/env python3
"""
Topological corridor identification — replaces geometric collinearity
with a semantic query: filter TLS by OSM road name in edge.getName().

This is the correct approach: SUMO preserves OSM street names in the
network file's edge name attribute. Querying "Memorial Drive" returns
TLS that actually control intersections ON Memorial Drive, not just
TLS that happen to be geographically near it.
"""
import json
import math
import os
import sys

SUMO_HOME = os.environ.get("SUMO_HOME", "/usr/share/sumo")
sys.path.insert(0, os.path.join(SUMO_HOME, "tools"))
import sumolib

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
NET_FILE = os.path.join(PROJECT_DIR, "calgary_downtown.net.xml")
TLS_POINTS_GEOJSON = os.path.join(PROJECT_DIR, "viz", "tls_points.geojson")
EDGES_GEOJSON = os.path.join(PROJECT_DIR, "viz", "edges.geojson")
OUTPUT_FILE = os.path.join(PROJECT_DIR, "output", "top_tls.json")

EARTH_RADIUS_M = 6371000.0


def haversine_m(a, b):
    lon1, lat1 = math.radians(a[1]), math.radians(a[0])
    lon2, lat2 = math.radians(b[1]), math.radians(b[0])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    x = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(x))


def find_tls_on_road(net, edge_volumes, tls_coords, keyword):
    """Return TLS where at least one controlled edge name contains keyword."""
    results = []
    for tls in net.getTrafficLights():
        tls_id = tls.getID()
        matched_name = None
        for edge in tls.getEdges():
            name = edge.getName() or ""
            if keyword.lower() in name.lower():
                matched_name = name
                break
        if matched_name:
            controlled = [
                {"id": e.getID(), "volume": edge_volumes.get(e.getID(), 0)}
                for e in tls.getEdges()
            ]
            results.append({
                "id": tls_id,
                "coordinates": tls_coords.get(tls_id, [0, 0]),
                "load_score": sum(c["volume"] for c in controlled),
                "road_name": matched_name,
                "n_edges": len(tls.getEdges()),
                "controlled_edges": controlled,
            })
    return results


def build_corridor(tls_set, road_name, axis="lon"):
    """Order TLS along the corridor and compute distances."""
    tls_set.sort(key=lambda t: t["coordinates"][0] if axis == "lon" else t["coordinates"][1])

    ordered = []
    prev_coord = None
    for i, t in enumerate(tls_set):
        dist = haversine_m(prev_coord, t["coordinates"]) if prev_coord else 0.0
        item = dict(t)
        item["order"] = i + 1
        item["distance_from_previous_m"] = round(dist, 1)
        ordered.append(item)
        prev_coord = t["coordinates"]

    if not ordered:
        return None

    bearing = 90.0 if axis == "lon" else 0.0
    span = haversine_m(ordered[0]["coordinates"], ordered[-1]["coordinates"])
    total_load = sum(t["load_score"] for t in ordered)

    return {
        "description": f"{road_name} ({'E-W' if axis == 'lon' else 'N-S'})",
        "bearing_degrees": round(bearing, 1),
        "tls_ids": [t["id"] for t in ordered],
        "members": ordered,
        "total_span_m": round(span, 1),
        "total_load": total_load,
        "method": "topological (edge name filter)",
        "filter_keyword": road_name,
    }


def main():
    os.makedirs(os.path.join(PROJECT_DIR, "output"), exist_ok=True)

    # Load supporting data
    with open(EDGES_GEOJSON) as f:
        edges_gj = json.load(f)
    edge_volumes = {
        feat["properties"]["id"]: feat["properties"].get("volume", 0)
        for feat in edges_gj["features"]
    }

    with open(TLS_POINTS_GEOJSON) as f:
        tls_gj = json.load(f)
    tls_coords = {
        feat["properties"]["id"]: feat["geometry"]["coordinates"]
        for feat in tls_gj["features"]
    }

    print("Loading SUMO network...")
    net = sumolib.net.readNet(NET_FILE)

    # Find corridors on both major arterials
    candidates = {}
    for road, axis in [("Memorial", "lon"), ("Macleod", "lat")]:
        tls_set = find_tls_on_road(net, edge_volumes, tls_coords, road)
        corridor = build_corridor(tls_set, road, axis)
        if corridor:
            candidates[road] = corridor
            print(f"\n{'='*85}")
            print(f"  {road} — {len(corridor['tls_ids'])} TLS | "
                  f"span {corridor['total_span_m']:.0f}m | "
                  f"total load {corridor['total_load']}")
            print(f"{'='*85}")
            for m in corridor["members"]:
                lon, lat = m["coordinates"]
                print(f"  #{m['order']:2d} ({lat:.6f}, {lon:.6f}) "
                      f"load={m['load_score']:>6} dist={m['distance_from_previous_m']:>7.1f}m  "
                      f"{m['id'][:50]}")

    # Pick the best corridor: prefer Macleod (higher load, denser spacing)
    # but allow override via CLI arg
    road_arg = sys.argv[1] if len(sys.argv) > 1 else None
    if road_arg and road_arg in candidates:
        chosen = road_arg
    else:
        chosen = max(candidates, key=lambda r: candidates[r]["total_load"])
        print(f"\n→ Auto-selected {chosen} (highest total load)")

    corridor = candidates[chosen]
    print(f"\n  Selected: {corridor['description']}")
    print(f"  TLS: {len(corridor['tls_ids'])}")
    print(f"  Span: {corridor['total_span_m']:.0f}m")
    print(f"  Method: {corridor['method']}")

    # Write in the same format that traci_corridor.py expects
    with open(OUTPUT_FILE, "w") as f:
        json.dump({
            "top20": [],
            "corridor": corridor,
            "all_corridors": {k: v for k, v in candidates.items()},
        }, f, indent=2)

    print(f"\n  Wrote {OUTPUT_FILE}")
    print(f"\n  Corridor TLS IDs:")
    print(json.dumps(corridor["tls_ids"], indent=2))


if __name__ == "__main__":
    main()
