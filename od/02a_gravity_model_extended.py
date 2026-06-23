#!/usr/bin/env python3
"""Build an internal + external OD matrix with EI, IE, and through trips."""
import json
import math

INTERNAL_JSON = "od/zone_stats.json"
EXTERNAL_JSON = "od/external_zones.json"
OUT_JSON = "od/od_matrix_extended.json"
OUT_REL = "od/od_matrix_extended.tazrelation.xml"

BETA = 0.5
THROUGH_SHARE = 0.30
MAX_ITER = 80
TOL = 0.01
OPPOSITES = {"N": "S", "S": "N", "E": "W", "W": "E", "NE": "SW", "SW": "NE", "NW": "SE", "SE": "NW"}


def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def deterrence(d):
    return math.exp(-BETA * d)


internal = json.load(open(INTERNAL_JSON))
external = json.load(open(EXTERNAL_JSON))
zone_data = {}
for zid, z in internal.items():
    zone_data[zid] = {
        "type": "internal",
        "sector": None,
        "centroid_lat": z["centroid_lat"],
        "centroid_lon": z["centroid_lon"],
        "production": float(z["production"]),
        "attraction": float(z["attraction"]),
    }
for zid, z in external.items():
    zone_data[zid] = {
        "type": "external",
        "sector": z["sector"],
        "centroid_lat": z["centroid_lat"],
        "centroid_lon": z["centroid_lon"],
        "production": float(z["production"]),
        "attraction": float(z["attraction"]),
    }

zone_ids = sorted(internal) + sorted(external)
N = len(zone_ids)
external_ids = [z for z in zone_ids if zone_data[z]["type"] == "external"]
internal_ids = [z for z in zone_ids if zone_data[z]["type"] == "internal"]

dist = [[0.0] * N for _ in range(N)]
for i, zi in enumerate(zone_ids):
    for j, zj in enumerate(zone_ids):
        if i == j:
            continue
        dist[i][j] = haversine_km(
            zone_data[zi]["centroid_lat"], zone_data[zi]["centroid_lon"],
            zone_data[zj]["centroid_lat"], zone_data[zj]["centroid_lon"],
        )
for i in range(N):
    nn = min(dist[i][j] for j in range(N) if j != i)
    dist[i][i] = max(nn * 0.5, 0.1)

P = [zone_data[z]["production"] for z in zone_ids]
A = [zone_data[z]["attraction"] for z in zone_ids]
T = [[0.0] * N for _ in range(N)]

for i, zi in enumerate(zone_ids):
    allowed = []
    for j, zj in enumerate(zone_ids):
        if i == j:
            continue
        ti, tj = zone_data[zi]["type"], zone_data[zj]["type"]
        if ti == "external" and tj == "external":
            continue
        allowed.append(j)
    denom = sum(A[j] * deterrence(dist[i][j]) for j in allowed) or 1e-10
    for j in allowed:
        T[i][j] = P[i] * A[j] * deterrence(dist[i][j]) / denom

# Seed explicit external-through flows before balancing.
idx = {z: i for i, z in enumerate(zone_ids)}
for zi in external_ids:
    opp_sector = OPPOSITES.get(zone_data[zi]["sector"])
    targets = [z for z in external_ids if zone_data[z]["sector"] == opp_sector]
    if not targets:
        targets = [z for z in external_ids if z != zi]
    if not targets:
        continue
    i = idx[zi]
    through = P[i] * THROUGH_SHARE
    share = through / len(targets)
    for zj in targets:
        T[i][idx[zj]] += share

for iteration in range(MAX_ITER):
    for i in range(N):
        row = sum(T[i])
        if row > 1e-10:
            factor = P[i] / row
            for j in range(N):
                T[i][j] *= factor
    max_err = 0.0
    for j in range(N):
        col = sum(T[i][j] for i in range(N))
        if col > 1e-10:
            factor = A[j] / col
            for i in range(N):
                T[i][j] *= factor
            max_err = max(max_err, abs(factor - 1))
    if max_err < TOL:
        print(f"Furness converged at iteration {iteration + 1} (max error {max_err:.4f})")
        break

totals = {
    "II": 0.0,
    "EI": 0.0,
    "IE": 0.0,
    "EE": 0.0,
}
for i, zi in enumerate(zone_ids):
    for j, zj in enumerate(zone_ids):
        if i == j:
            continue
        key = zone_data[zi]["type"][0].upper() + zone_data[zj]["type"][0].upper()
        totals[key] += T[i][j]

matrix = {
    "zones": zone_ids,
    "zone_types": {z: zone_data[z]["type"] for z in zone_ids},
    "P": [round(x) for x in P],
    "A": [round(x) for x in A],
    "T": [[round(T[i][j]) for j in range(N)] for i in range(N)],
    "dist": [[round(dist[i][j], 3) for j in range(N)] for i in range(N)],
    "flow_totals": {k: round(v) for k, v in totals.items()},
}
json.dump(matrix, open(OUT_JSON, "w"), indent=2)

with open(OUT_REL, "w") as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n<tazRelations>\n')
    for i, zi in enumerate(zone_ids):
        for j, zj in enumerate(zone_ids):
            trips = int(round(T[i][j]))
            if trips > 0:
                f.write(f'  <tazRelation from="{zi}" to="{zj}" count="{trips}"/>\n')
    f.write("</tazRelations>\n")

print(f"Zones: {len(internal_ids)} internal + {len(external_ids)} external")
print(f"Total OD trips: {sum(sum(r) for r in T):.0f}")
print("Flow totals: " + ", ".join(f"{k}={v:.0f}" for k, v in totals.items()))
print(f"Wrote {OUT_JSON}")
print(f"Wrote {OUT_REL}")
