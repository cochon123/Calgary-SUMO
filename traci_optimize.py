#!/usr/bin/env python3
"""
TraCI Signal Optimization Loop — Proof of Concept (v2)

Simplified approach:
  1. Run ALL passes via TraCI (including baseline) for consistency
  2. Track vehicles passing through the target TLS
  3. Use a subset of routes filtered to those crossing the target intersection
  4. Measure travel time for affected vehicles
"""
import sys
import os
import json
import subprocess
import statistics
from collections import defaultdict

SUMO_HOME = os.environ.get("SUMO_HOME", "/usr/share/sumo")
sys.path.insert(0, os.path.join(SUMO_HOME, "tools"))
import traci

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
NET_FILE = os.path.join(PROJECT_DIR, "calgary_downtown.net.xml")
ROUTE_FILE = os.path.join(PROJECT_DIR, "od", "calgary_od_extended_calibrated.rou.xml")

# Target TLS — Macleod Trail intersection
TARGET_TLS = "cluster_30727620_6311428931_6311428932_6311428934_#1more"

SIM_STEPS = 3600

# Signal plans: (label, ns_green_sec, ew_green_sec, yellow_sec)
SIGNAL_PLANS = [
    ("baseline",    42, 42, 3),   # Original from network
    ("ns_favor",    60, 24, 3),   # Favor NS (Macleod Trail)
    ("ew_favor",    24, 60, 3),   # Favor EW cross street
    ("balanced_60", 27, 27, 3),   # Shorter cycle, balanced
    ("long_green",  70, 70, 3),   # Long greens both ways
]


def run_traci_pass(label, ns_green, ew_green, yellow):
    """Run one simulation pass with TraCI, applying signal changes."""
    print(f"  Running {label}...", end=" ", flush=True)

    sumo_cmd = [
        os.path.join(SUMO_HOME, "bin", "sumo"),
        "-n", NET_FILE,
        "-r", ROUTE_FILE,
        "--begin", "0",
        "--end", str(SIM_STEPS),
        "--time-to-teleport", "300",
        "--max-depart-delay", "600",
        "--no-step-log",
        "--no-warnings",
        "--tripinfo-output", os.path.join(PROJECT_DIR, "output", f"tripinfo_{label}.xml"),
    ]

    traci.start(sumo_cmd)
    print("connected", end=" ", flush=True)

    # Apply signal plan at step 1
    tls_ids = traci.trafficlight.getIDList()
    if TARGET_TLS in tls_ids:
        # Get the controlled lanes
        controlled_lanes = traci.trafficlight.getControlledLanes(TARGET_TLS)
        
        # Get current program
        logics = traci.trafficlight.getAllProgramLogics(TARGET_TLS)
        if logics:
            prog = logics[0]
            phases = prog.getPhases()
            
            if label != "baseline" and len(phases) >= 4:
                # Modify the standard 4-phase program
                # Phase 0: NS green, Phase 1: NS yellow
                # Phase 2: EW green, Phase 3: EW yellow
                phases[0].duration = ns_green
                phases[1].duration = yellow
                phases[2].duration = ew_green
                phases[3].duration = yellow
                
                modified = traci.trafficlight.Logic(
                    prog.programID,
                    0,       # type (int)
                    0,       # currentPhaseIndex
                    phases,  # phases list
                )
                traci.trafficlight.setProgramLogic(TARGET_TLS, modified)

    # Track metrics
    vehicle_data = {}  # veh_id -> {depart, arrival, duration, waitingTime, timeLoss}
    tls_vehicle_ids = set()
    
    # Get lanes controlled by target TLS
    if TARGET_TLS in tls_ids:
        target_lanes = set(traci.trafficlight.getControlledLanes(TARGET_TLS))
    else:
        target_lanes = set()

    step = 0
    while step < SIM_STEPS:
        traci.simulationStep()
        
        # Track vehicles on target TLS lanes
        for vid in traci.vehicle.getIDList():
            try:
                lane = traci.vehicle.getLaneID(vid)
                if lane in target_lanes:
                    tls_vehicle_ids.add(vid)
            except:
                pass
        
        step += 1

    # Get tripinfo-style data for all arrived vehicles
    arrived = traci.simulation.getArrivedIDList()
    for vid in arrived:
        try:
            # Use traci to get final stats — but vehicle is already gone
            # We need to have tracked these during the simulation
            pass
        except:
            pass

    traci.close()

    # Parse the tripinfo output instead
    tripinfo_file = os.path.join(PROJECT_DIR, "output", f"tripinfo_{label}.xml")
    return parse_tripinfo(tripinfo_file), len(tls_vehicle_ids)


def parse_tripinfo(tripinfo_file):
    """Extract metrics from tripinfo.xml."""
    import xml.etree.ElementTree as ET
    
    trips = []
    if not os.path.exists(tripinfo_file):
        return {"n_trips": 0}
    
    context = ET.iterparse(tripinfo_file, events=("end",))
    for _, elem in context:
        if elem.tag == "tripinfo":
            trips.append({
                "id": elem.get("id", ""),
                "duration": float(elem.get("duration", 0)),
                "waitingTime": float(elem.get("waitingTime", 0)),
                "timeLoss": float(elem.get("timeLoss", 0)),
                "depart": float(elem.get("depart", 0)),
                "arrival": float(elem.get("arrival", 0)),
            })
            elem.clear()

    if not trips:
        return {"n_trips": 0}

    completed = [t for t in trips if t["arrival"] < SIM_STEPS + 1]
    return {
        "n_trips": len(trips),
        "n_completed": len(completed),
        "avg_duration": statistics.mean(t["duration"] for t in completed),
        "avg_wait": statistics.mean(t["waitingTime"] for t in completed),
        "avg_time_loss": statistics.mean(t["timeLoss"] for t in completed),
        "total_wait": sum(t["waitingTime"] for t in completed),
        "median_duration": statistics.median(t["duration"] for t in completed),
    }


def main():
    os.makedirs(os.path.join(PROJECT_DIR, "output"), exist_ok=True)

    print("=" * 70)
    print("  TRACI SIGNAL OPTIMIZATION — PROOF OF CONCEPT v2")
    print("=" * 70)
    print(f"  Target TLS: {TARGET_TLS}")
    print(f"  Routes: {os.path.basename(ROUTE_FILE)}")
    print(f"  Steps: {SIM_STEPS} ({SIM_STEPS/60:.0f} min)")
    print(f"  Plans: {len(SIGNAL_PLANS)}")
    print()

    # Add tripinfo output to command
    results = {}
    for label, ns_green, ew_green, yellow in SIGNAL_PLANS:
        metrics, n_tls_vehicles = run_traci_pass(label, ns_green, ew_green, yellow)
        results[label] = {**metrics, "n_tls_vehicles": n_tls_vehicles}
        print(f"done")
        if metrics.get("n_trips", 0) > 0:
            print(f"    Trips: {metrics['n_trips']:>5} | "
                  f"TLS vehicles: {n_tls_vehicles:>4} | "
                  f"Avg dur: {metrics['avg_duration']:>6.1f}s | "
                  f"Avg wait: {metrics['avg_wait']:>5.1f}s")

    # Compare
    print(f"\n{'='*70}")
    print("  RESULTS")
    print(f"{'='*70}")

    baseline = results.get("baseline", {})
    b_dur = baseline.get("avg_duration", 0)
    b_wait = baseline.get("avg_wait", 0)

    print(f"\n  {'Plan':<15} {'Trips':>6} {'TLSVeh':>6} {'Duration':>9} "
          f"{'Wait':>7} {'Δ Dur':>8} {'Δ Wait':>8}")
    print(f"  {'-'*15} {'-'*6} {'-'*6} {'-'*9} {'-'*7} {'-'*8} {'-'*8}")

    for label, _, _, _ in SIGNAL_PLANS:
        m = results[label]
        if m.get("n_trips", 0) == 0:
            print(f"  {label:<15} --- no data ---")
            continue

        delta_dur = m["avg_duration"] - b_dur
        delta_wait = m["avg_wait"] - b_wait

        print(f"  {label:<15} {m['n_trips']:>6} {m.get('n_tls_vehicles',0):>6} "
              f"{m['avg_duration']:>7.1f}s {m['avg_wait']:>5.1f}s "
              f"{delta_dur:>+6.1f}s {delta_wait:>+6.1f}s")

    # Save
    results_file = os.path.join(PROJECT_DIR, "output", "traci_optimization_results.json")
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Verdict
    print(f"\n{'='*70}")
    non_baseline = {l: r for l, r in results.items() if l != "baseline" and r.get("n_trips", 0) > 0}
    if non_baseline:
        best_label = min(non_baseline, key=lambda l: non_baseline[l]["avg_duration"])
        best = non_baseline[best_label]
        delta = best["avg_duration"] - b_dur
        print(f"  Best plan: {best_label} (avg duration {best['avg_duration']:.1f}s)")
        if abs(delta) > 0.5:
            direction = "improvement" if delta < 0 else "degradation"
            print(f"  Δ = {delta:+.1f}s ({direction})")
            print(f"  → FEEDBACK LOOP CONFIRMED: signal change produced measurable {direction}")
        else:
            print(f"  Δ = {delta:+.1f}s (negligible)")
            print(f"  → Loop runs but effect too small (check TLS vehicle count)")


if __name__ == "__main__":
    main()
