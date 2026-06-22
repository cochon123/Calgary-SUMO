#!/usr/bin/env python3
"""
Generate calibrated SUMO demand from AADT volumes.

Strategy: Each AADT measurement gives daily weekday volume on a specific edge.
We scale daily -> hourly peak (factor ~0.10), then generate vehicle flows
that inject traffic onto calibrated edges and route them to random exits.

This creates a .rou.xml with <flow> elements proportional to real AADT.
"""
import csv, random, sys, os, math

SUMO_HOME = os.environ.get('SUMO_HOME', '/usr/share/sumo')
sys.path.insert(0, os.path.join(SUMO_HOME, 'tools'))
import sumolib

NET_FILE = 'calgary_downtown.net.xml'
EDGE_VOL_CSV = 'aadt/edge_volumes.csv'
OUT_ROU = 'output/calgary_aadt.rou.xml'

# Peak hour factor: ADWT * PHF ≈ hourly vehicles during peak hour.
# Calgary AM peak ≈ 8-10% of daily volume. We use 0.09 for realism.
PEAK_HOUR_FACTOR = 0.09
SIM_DURATION = 3600  # 1 hour simulation

print("Loading network...")
net = sumolib.net.readNet(NET_FILE)

# Read AADT-matched edges, filter to passenger-accessible only
edge_vols = []
skipped = 0
with open(EDGE_VOL_CSV) as f:
    reader = csv.DictReader(f)
    for row in reader:
        eid = row['edge_id']
        # Check if edge allows passenger vehicles
        edge = net.getEdge(eid)
        if edge is None:
            skipped += 1
            continue
        # Check lane permissions — skip if no passenger access
        allows_car = False
        for lane in edge.getLanes():
            if lane.allows('passenger'):
                allows_car = True
                break
        if not allows_car:
            skipped += 1
            continue
        edge_vols.append({
            'edge': eid,
            'volume': float(row['aadt_volume']),
            'name': row['section_name'],
        })
print(f"Calibrated edges: {len(edge_vols)} (skipped {skipped} non-passenger)")

# Compute hourly flow per edge
total_hourly = 0
for ev in edge_vols:
    ev['hourly'] = int(ev['volume'] * PEAK_HOUR_FACTOR)
    total_hourly += ev['hourly']
print(f"Total peak-hour vehicles: {total_hourly}")
print(f"Avg vehicles per edge: {total_hourly/len(edge_vols):.0f}")

# For each calibrated edge, we need a destination.
# Strategy: pick a random other calibrated edge as destination,
# weighted by its volume (bigger roads = more attractive destinations).
random.seed(42)

# Collect all valid edges and their "from" nodes
valid_edges = [ev['edge'] for ev in edge_vols if ':' not in ev['edge']]

# Generate flows
print("\nGenerating flows...")
with open(OUT_ROU, 'w') as f:
    f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
    f.write('<routes>\n')
    f.write('  <vType id="car" vClass="passenger" maxSpeed="33.33" accel="2.6" decel="4.5" sigma="0.5" length="5" minGap="2.5" color="0.2,0.5,0.8"/>\n')
    f.write('  <vType id="truck" vClass="truck" maxSpeed="25" accel="1.5" decel="4.0" sigma="0.5" length="10" minGap="3" color="0.8,0.3,0.2" probability="0.08"/>\n\n')

    flow_id = 0
    for ev in edge_vols:
        src_edge = ev['edge']
        n_veh = ev['hourly']
        if n_veh < 1:
            continue

        # Pick a destination edge (weighted random, not same as source)
        dest_candidates = [e for e in valid_edges if e != src_edge]
        if not dest_candidates:
            continue

        # Probability per flow: inject vehicles evenly over the simulation
        # period with a "probability" depart attribute
        depart_prob = n_veh / SIM_DURATION

        # Create 1-3 flows per source edge with different destinations
        n_dests = min(3, max(1, n_veh // 30))
        chosen_dests = random.sample(dest_candidates, min(n_dests, len(dest_candidates)))

        for dest_edge in chosen_dests:
            veh_share = depart_prob / n_dests
            f.write(f'  <flow id="aadt_{flow_id}" type="car" '
                    f'from="{src_edge}" to="{dest_edge}" '
                    f'begin="0" end="{SIM_DURATION}" '
                    f'probability="{veh_share:.6f}" '
                    f'departLane="free" departSpeed="random"/>\n')
            flow_id += 1

    f.write('</routes>\n')

print(f"Written {flow_id} flows to {OUT_ROU}")
print(f"\nExpected total vehicles ≈ {total_hourly}")
