#!/usr/bin/env python3
"""
Step 2: Build OD matrix using a gravity model.

Gravity model:
    T(i,j) = P_i * A_j * f(d_ij)
where:
    P_i     = production of zone i (trips originating)
    A_j     = attraction of zone j (trips terminating)
    f(d)    = deterrence function = exp(-beta * d)
    d_ij    = great-circle distance between zone centroids (km)

The matrix is doubly constrained (Furness method) so that:
    sum_j T(i,j) = P_i  for all i
    sum_i T(i,j) = A_j  for all j

Outputs:
  - od/od_matrix.tazrelation.xml   (SUMO tazRelation format for marouter)
"""
import json, math, os

def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km."""
    R = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

# Load zone data
zones = json.load(open('od/zone_stats.json'))
zone_ids = sorted(zones.keys())
N = len(zone_ids)
print(f"Zones: {N}")

# --- Distance matrix ---------------------------------------------------------
print("Computing distance matrix...")
dist = [[0.0]*N for _ in range(N)]
for i, zi in enumerate(zone_ids):
    for j, zj in enumerate(zone_ids):
        if i == j:
            dist[i][j] = 0.0
        else:
            dist[i][j] = haversine_km(
                zones[zi]['centroid_lat'], zones[zi]['centroid_lon'],
                zones[zj]['centroid_lat'], zones[zj]['centroid_lon']
            )

# --- Intra-zone distance estimate (half the nearest neighbor distance) ------
for i in range(N):
    min_d = min(dist[i][j] for j in range(N) if j != i)
    dist[i][i] = max(min_d * 0.5, 0.1)

# --- Deterrence function -----------------------------------------------------
# beta controls how quickly demand decays with distance.
# For urban areas, typical beta ≈ 0.3-0.8 per km.
BETA = 0.5
print(f"Deterrence: f(d) = exp(-{BETA} * d)")

def deterrence(d):
    return math.exp(-BETA * d)

# --- Build raw gravity matrix ------------------------------------------------
print("Building gravity matrix...")
T = [[0.0]*N for _ in range(N)]
P = [zones[zid]['production'] for zid in zone_ids]
A = [zones[zid]['attraction'] for zid in zone_ids]

for i in range(N):
    denom = sum(A[j] * deterrence(dist[i][j]) for j in range(N))
    if denom < 1e-10:
        denom = 1e-10
    for j in range(N):
        T[i][j] = P[i] * A[j] * deterrence(dist[i][j]) / denom

# --- Furness (IPF) doubly-constrained balancing ------------------------------
print("Running Furness iterations (doubly-constrained)...")
MAX_ITER = 50
TOL = 0.01

for iteration in range(MAX_ITER):
    # Row balancing: scale rows to match productions
    for i in range(N):
        row_sum = sum(T[i][j] for j in range(N))
        if row_sum > 1e-10:
            factor = P[i] / row_sum
            for j in range(N):
                T[i][j] *= factor

    # Column balancing: scale columns to match attractions
    max_err = 0
    for j in range(N):
        col_sum = sum(T[i][j] for i in range(N))
        if col_sum > 1e-10:
            factor = A[j] / col_sum
            for i in range(N):
                T[i][j] *= factor
            max_err = max(max_err, abs(factor - 1.0))

    if max_err < TOL:
        print(f"  Converged at iteration {iteration+1} (max error: {max_err:.4f})")
        break

# --- Report ------------------------------------------------------------------
total_trips = sum(T[i][j] for i in range(N) for j in range(N))
intra_zone = sum(T[i][i] for i in range(N))
print(f"\nTotal OD trips: {total_trips:.0f}")
print(f"Intra-zone trips: {intra_zone:.0f} ({100*intra_zone/total_trips:.1f}%)")
print(f"\nTop 10 OD pairs:")
pairs = [(T[i][j], zone_ids[i], zone_ids[j], dist[i][j]) for i in range(N) for j in range(N) if i != j]
pairs.sort(reverse=True)
for trips, o, d, dist_km in pairs[:10]:
    print(f"  {trips:>7.0f}  {o} -> {d}  ({dist_km:.2f} km)")

# --- Write tazRelation file for marouter ------------------------------------
print("\nWriting od/od_matrix.tazrelation.xml...")
with open('od/od_matrix.tazrelation.xml', 'w') as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    f.write('<tazRelations>\n')
    for i in range(N):
        for j in range(N):
            trips = int(round(T[i][j]))
            if trips > 0:
                f.write(f'  <tazRelation from="{zone_ids[i]}" to="{zone_ids[j]}" '
                        f'count="{trips}"/>\n')
    f.write('</tazRelations>\n')

print(f"  {sum(1 for i in range(N) for j in range(N) if T[i][j] > 0)} OD pairs written")

# Also save the matrix as JSON for the calibration step
matrix_data = {
    'zones': zone_ids,
    'P': P,
    'A': A,
    'T': [[round(T[i][j]) for j in range(N)] for i in range(N)],
    'dist': [[round(dist[i][j], 3) for j in range(N)] for i in range(N)],
}
json.dump(matrix_data, open('od/od_matrix.json', 'w'), indent=2)
print("  Matrix saved to od/od_matrix.json")
