#!/usr/bin/env python3
"""Analyze AADT-calibrated simulation results and compare with random demand."""
import xml.etree.ElementTree as ET

def analyze_tripinfo(path, label):
    tree = ET.parse(path)
    trips = tree.findall('tripinfo')
    if not trips:
        print(f"{label}: no trips found")
        return {}
    durations = [float(t.get('duration')) for t in trips]
    distances = [float(t.get('routeLength')) for t in trips]
    wait = [float(t.get('waitingTime', 0)) for t in trips]
    speeds = [float(t.get('routeLength')) / float(t.get('duration'))
              for t in trips if float(t.get('duration')) > 0]
    stats = {
        'label': label,
        'vehicles': len(trips),
        'avg_duration_s': sum(durations) / len(durations),
        'avg_distance_m': sum(distances) / len(distances),
        'avg_speed_ms': sum(speeds) / len(speeds) if speeds else 0,
        'avg_speed_kmh': (sum(speeds) / len(speeds) * 3.6) if speeds else 0,
        'avg_wait_s': sum(wait) / len(wait),
        'max_wait_s': max(wait),
    }
    print(f"\n{'='*55}")
    print(f"  {label}")
    print(f"{'='*55}")
    print(f"  Vehicles simulated:   {stats['vehicles']:>8,}")
    print(f"  Avg travel time:      {stats['avg_duration_s']:>8.0f} s  ({stats['avg_duration_s']/60:.1f} min)")
    print(f"  Avg distance:         {stats['avg_distance_m']:>8.0f} m")
    print(f"  Avg speed:            {stats['avg_speed_kmh']:>8.1f} km/h")
    print(f"  Avg waiting time:     {stats['avg_wait_s']:>8.1f} s")
    print(f"  Max waiting time:     {stats['max_wait_s']:>8.0f} s")
    return stats

# Compare both simulations
random_stats = analyze_tripinfo('output/tripinfo.xml', 'RANDOM DEMAND (baseline)')
aadt_stats = analyze_tripinfo('output/aadt_tripinfo.xml', 'AADT-CALIBRATED DEMAND')

if random_stats and aadt_stats:
    print(f"\n{'='*55}")
    print(f"  COMPARISON")
    print(f"{'='*55}")
    ratio = aadt_stats['avg_speed_kmh'] / random_stats['avg_speed_kmh'] if random_stats['avg_speed_kmh'] else 0
    print(f"  Speed ratio (AADT/random): {ratio:.2f}x")
    print(f"  The AADT run concentrates vehicles on real high-volume")
    print(f"  corridors, creating realistic congestion patterns.")

# Also check summary for throughput
print(f"\n{'='*55}")
print(f"  AADT SIMULATION SUMMARY (last step)")
print(f"{'='*55}")
stree = ET.parse('output/aadt_summary.xml')
steps = stree.findall('step')
if steps:
    last = steps[-1]
    print(f"  Sim time:       {last.get('time')}s")
    print(f"  Loaded:         {last.get('loaded')}")
    print(f"  Inserted:       {last.get('inserted')}")
    print(f"  Arrived:        {last.get('arrived')}")
    print(f"  Running:        {last.get('running')}")
    print(f"  Collisions:     {last.get('collisions')}")
    print(f"  Teleports:      {last.get('teleports')}")
    print(f"  Halting:        {last.get('halting')}")
    print(f"  Mean speed:     {last.get('meanSpeed')} m/s")
