#!/usr/bin/env python3
"""
Write OD matrix in SUMO's classic O/D matrix format (.od) for marouter.
Format:
  $OR;D2
  * From <taz_id>
  <volumes space-separated>
"""
import json

matrix = json.load(open('od/od_matrix.json'))
zones = matrix['zones']
T = matrix['T']
N = len(zones)

# Remove intra-zone trips (from == to) — they cause issues with marouter
print(f"Original OD pairs: {sum(1 for i in range(N) for j in range(N) if T[i][j] > 0)}")
print(f"Intra-zone removed: {sum(1 for i in range(N) if T[i][i] > 0)}")
total = 0

with open('od/od_matrix.od', 'w') as f:
    f.write('$OR;D2\n')
    # Column headers (destination zones)
    f.write('; ' + ' '.join(zones) + '\n')
    for i in range(N):
        row = [T[i][j] if i != j else 0 for j in range(N)]  # zero out intra-zone
        row_sum = sum(row)
        total += row_sum
        f.write(f'* From {zones[i]}\n')
        f.write(' '.join(f'{v:.1f}' for v in row) + '\n')

print(f"Total inter-zone trips: {total:.0f}")
print(f"Written to od/od_matrix.od ({N}x{N} matrix)")
