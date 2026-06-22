#!/usr/bin/env python3
"""
Step 3: Generate TAZ-based flows for marouter UE assignment.

Instead of relying on the O/D matrix format, we generate <flow> elements
with fromTaz/toTaz attributes. marouter will perform UE assignment on these.
"""
import json

matrix = json.load(open('od/od_matrix.json'))
zones = matrix['zones']
T = matrix['T']
N = len(zones)

# Simulation period
BEGIN = 0
END = 3600

total_vehicles = 0
with open('od/od_flows.rou.xml', 'w') as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    f.write('<routes>\n')
    f.write('  <vType id="car" vClass="passenger" maxSpeed="33.33" '
            'accel="2.6" decel="4.5" sigma="0.5" length="5" minGap="2.5"/>\n\n')

    flow_id = 0
    for i in range(N):
        for j in range(N):
            if i == j:
                continue  # skip intra-zone
            trips = int(round(T[i][j]))
            if trips < 1:
                continue
            # Distribute vehicles evenly over the hour
            # Use 'number' attribute for fixed-count flows
            f.write(f'  <flow id="flow_{flow_id}" type="car" '
                    f'fromTaz="{zones[i]}" toTaz="{zones[j]}" '
                    f'begin="{BEGIN}" end="{END}" '
                    f'vehsPerHour="{trips}" '
                    f'departLane="free" departSpeed="random"/>\n')
            flow_id += 1
            total_vehicles += trips

    f.write('</routes>\n')

print(f"Flows written: {flow_id}")
print(f"Total vehicles/hour: {total_vehicles}")
print(f"Written to od/od_flows.rou.xml")
