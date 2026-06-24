#!/usr/bin/env python3
"""Run coordinated multi-TLS corridor signal plans with TraCI."""
import copy
import json
import math
import os
import random
import statistics
import sys

SUMO_HOME = os.environ.get("SUMO_HOME", "/usr/share/sumo")
sys.path.insert(0, os.path.join(SUMO_HOME, "tools"))
import traci

from traci_optimize import parse_tripinfo

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
NET_FILE = os.path.join(PROJECT_DIR, "calgary_downtown.net.xml")
ROUTE_FILE = os.path.join(PROJECT_DIR, "od", "calgary_od_extended_calibrated.rou.xml")
TOP_TLS_FILE = os.path.join(PROJECT_DIR, "output", "top_tls.json")
SIM_STEPS = 3600
YELLOW = 3
RANDOM_SEED = 42
EARTH_RADIUS_M = 6371000.0
SCALE = float(os.environ.get("CORRIDOR_SCALE", "1.0"))
RESULTS_FILE = os.path.join(
    PROJECT_DIR,
    "output",
    f"corridor_optimization_results_scale{int(SCALE * 100)}.json",
)


def haversine_m(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    x = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(x))


def load_corridor():
    with open(TOP_TLS_FILE) as f:
        data = json.load(f)
    members = data["corridor"]["members"]
    return data["corridor"], [m["id"] for m in members], {m["id"]: m["coordinates"] for m in members}


def cumulative_distances(tls_ids, coords):
    distances = {tls_ids[0]: 0.0}
    total = 0.0
    for prev, cur in zip(tls_ids, tls_ids[1:]):
        total += haversine_m(coords[prev], coords[cur])
        distances[cur] = total
    return distances


def build_plans(tls_ids, coords):
    distances = cumulative_distances(tls_ids, coords)
    rng = random.Random(RANDOM_SEED)
    return [
        {"name": "baseline", "cycle": None, "ns_green": None, "ew_green": None, "yellow": None, "offsets": {}},
        {"name": "uniform_short", "cycle": 60, "ns_green": 27, "ew_green": 27, "yellow": YELLOW, "offsets": {t: 0 for t in tls_ids}},
        {"name": "uniform_long", "cycle": 90, "ns_green": 42, "ew_green": 42, "yellow": YELLOW, "offsets": {t: 0 for t in tls_ids}},
        {
            "name": "green_wave_ns",
            "cycle": 75,
            "ns_green": 45,
            "ew_green": 24,
            "yellow": YELLOW,
            "offsets": {t: distances[t] / 14.0 for t in tls_ids},
        },
        {
            "name": "green_wave_ew",
            "cycle": 75,
            "ns_green": 24,
            "ew_green": 45,
            "yellow": YELLOW,
            "offsets": {t: distances[t] / 14.0 for t in tls_ids},
        },
        {
            "name": "green_wave_ns_fast",
            "cycle": 75,
            "ns_green": 45,
            "ew_green": 24,
            "yellow": YELLOW,
            "offsets": {t: distances[t] / (60 / 3.6) for t in tls_ids},
        },
        {
            "name": "random_offsets",
            "cycle": 75,
            "ns_green": 45,
            "ew_green": 24,
            "yellow": YELLOW,
            "offsets": {t: rng.uniform(0, 75) for t in tls_ids},
        },
    ]


def phase_at_offset(phases, offset):
    cycle = sum(float(p.duration) for p in phases)
    offset = offset % cycle
    elapsed = 0.0
    for i, phase in enumerate(phases):
        elapsed += float(phase.duration)
        if offset < elapsed:
            return i
    return 0


def apply_plan_to_tls(tls_id, plan):
    logics = traci.trafficlight.getAllProgramLogics(tls_id)
    if not logics:
        return False, "no program logic"
    prog = logics[0]
    phases = list(copy.deepcopy(prog.getPhases()))
    if len(phases) < 4:
        return False, f"{len(phases)} phases; need at least 4"

    phases[0].duration = plan["ns_green"]
    phases[1].duration = plan["yellow"]
    phases[2].duration = plan["ew_green"]
    phases[3].duration = plan["yellow"]
    if len(phases) > 4:
        for i in range(4, len(phases)):
            if "y" in phases[i].state.lower() and "g" not in phases[i].state.lower():
                phases[i].duration = plan["yellow"]

    phase_index = phase_at_offset(phases, plan["offsets"].get(tls_id, 0.0))
    logic = traci.trafficlight.Logic(prog.programID, prog.type, phase_index, phases)
    traci.trafficlight.setProgramLogic(tls_id, logic)
    traci.trafficlight.setPhase(tls_id, phase_index)
    return True, f"{len(phases)} phases"


def corridor_metrics(tripinfo_file, corridor_vehicle_ids):
    import xml.etree.ElementTree as ET

    trips = []
    if not os.path.exists(tripinfo_file):
        return {"corridor_n_vehicles": len(corridor_vehicle_ids)}
    for _, elem in ET.iterparse(tripinfo_file, events=("end",)):
        if elem.tag == "tripinfo" and elem.get("id") in corridor_vehicle_ids:
            trips.append(
                {
                    "duration": float(elem.get("duration", 0)),
                    "waitingTime": float(elem.get("waitingTime", 0)),
                    "timeLoss": float(elem.get("timeLoss", 0)),
                }
            )
            elem.clear()
    if not trips:
        return {"corridor_n_vehicles": len(corridor_vehicle_ids), "corridor_avg_duration": 0}
    return {
        "corridor_n_vehicles": len(trips),
        "corridor_observed_vehicles": len(corridor_vehicle_ids),
        "corridor_avg_duration": statistics.mean(t["duration"] for t in trips),
        "corridor_avg_wait": statistics.mean(t["waitingTime"] for t in trips),
        "corridor_avg_time_loss": statistics.mean(t["timeLoss"] for t in trips),
    }


def run_plan(plan, tls_ids):
    label = plan["name"]
    tripinfo_file = os.path.join(PROJECT_DIR, "output", f"tripinfo_corridor_{label}.xml")
    print(f"  Running {label}...", end=" ", flush=True)
    sumo_cmd = [
        os.path.join(SUMO_HOME, "bin", "sumo"),
        "-n",
        NET_FILE,
        "-r",
        ROUTE_FILE,
        "--begin",
        "0",
        "--end",
        str(SIM_STEPS),
        "--scale",
        str(SCALE),
        "--time-to-teleport",
        "300",
        "--max-depart-delay",
        "600",
        "--no-step-log",
        "--no-warnings",
        "--tripinfo-output",
        tripinfo_file,
    ]
    traci.start(sumo_cmd)

    available = set(traci.trafficlight.getIDList())
    corridor_lanes = set()
    applied = {}
    warnings = []
    for tls_id in tls_ids:
        if tls_id not in available:
            warnings.append(f"{tls_id}: missing from TraCI")
            continue
        corridor_lanes.update(traci.trafficlight.getControlledLanes(tls_id))
        if label != "baseline":
            ok, note = apply_plan_to_tls(tls_id, plan)
            applied[tls_id] = ok
            if not ok:
                warnings.append(f"{tls_id}: skipped ({note})")
        else:
            applied[tls_id] = True

    corridor_vehicle_ids = set()
    for _ in range(SIM_STEPS):
        traci.simulationStep()
        for vid in traci.vehicle.getIDList():
            try:
                if traci.vehicle.getLaneID(vid) in corridor_lanes:
                    corridor_vehicle_ids.add(vid)
            except traci.TraCIException:
                pass
    traci.close()

    metrics = parse_tripinfo(tripinfo_file)
    metrics.update(corridor_metrics(tripinfo_file, corridor_vehicle_ids))
    metrics["total_waiting_time"] = metrics.get("total_wait", 0)
    metrics["applied_tls"] = sum(1 for ok in applied.values() if ok)
    metrics["skipped_tls"] = [tls_id for tls_id, ok in applied.items() if not ok]
    metrics["warnings"] = warnings
    print("done", flush=True)
    return metrics


def print_table(results):
    baseline = results["baseline"]
    bdur = baseline.get("avg_duration", 0)
    bwait = baseline.get("avg_wait", 0)
    rows = sorted(results.items(), key=lambda kv: kv[1].get("avg_duration", float("inf")))
    print("\nComparison sorted by network-wide avg_duration")
    print(
        f"{'Plan':<20} {'Trips':>6} {'Done':>6} {'AvgDur':>8} {'Delta':>8} "
        f"{'AvgWait':>8} {'DWait':>8} {'CorrVeh':>7} {'CorrDur':>8}"
    )
    for name, m in rows:
        print(
            f"{name:<20} {m.get('n_trips', 0):>6} {m.get('n_completed', 0):>6} "
            f"{m.get('avg_duration', 0):>8.1f} {m.get('avg_duration', 0) - bdur:>+8.1f} "
            f"{m.get('avg_wait', 0):>8.1f} {m.get('avg_wait', 0) - bwait:>+8.1f} "
            f"{m.get('corridor_n_vehicles', 0):>7} {m.get('corridor_avg_duration', 0):>8.1f}"
        )


def verdict(results):
    baseline = results["baseline"]["avg_duration"]
    uniform = min(results[p]["avg_duration"] for p in ("uniform_short", "uniform_long"))
    wave_names = ("green_wave_ns", "green_wave_ew", "green_wave_ns_fast")
    best_wave_name = min(wave_names, key=lambda p: results[p]["avg_duration"])
    best_wave = results[best_wave_name]["avg_duration"]
    best_name = min(results, key=lambda p: results[p].get("avg_duration", float("inf")))
    print("\nVerdict")
    print(f"Best overall: {best_name} ({results[best_name]['avg_duration']:.1f}s avg duration)")
    print(f"Best green wave: {best_wave_name} ({best_wave - baseline:+.1f}s vs baseline)")
    print(f"Best uniform split delta: {uniform - baseline:+.1f}s vs baseline")
    if best_wave < uniform:
        print("Coordination beat the best uniform split on network-wide average duration.")
    else:
        print("Coordination did not beat the best uniform split on network-wide average duration.")
    if best_wave < baseline:
        print("Coordination beat the baseline.")
    else:
        print("Coordination did not beat the baseline.")


def main():
    os.makedirs(os.path.join(PROJECT_DIR, "output"), exist_ok=True)
    corridor, tls_ids, coords = load_corridor()
    plans = build_plans(tls_ids, coords)

    print("=" * 72)
    print("  TRACI CORRIDOR OPTIMIZATION")
    print("=" * 72)
    print(f"Corridor: {corridor['description']} | bearing {corridor['bearing_degrees']} deg")
    print(f"TLS count: {len(tls_ids)}")
    print(f"Simulation steps: {SIM_STEPS}")
    print(f"Scale: {SCALE} (≈{int(SCALE * 19000)} veh)")
    print()

    results = {}
    for plan in plans:
        metrics = run_plan(plan, tls_ids)
        results[plan["name"]] = metrics
        if metrics.get("n_trips", 0):
            print(
                f"    Trips: {metrics['n_trips']} | Completed: {metrics.get('n_completed', 0)} | "
                f"Avg dur: {metrics.get('avg_duration', 0):.1f}s | "
                f"Corridor veh: {metrics.get('corridor_n_vehicles', 0)} | "
                f"Applied TLS: {metrics.get('applied_tls', 0)}"
            )
        for warning in metrics.get("warnings", []):
            print(f"    WARNING: {warning}")

    payload = {
        "corridor": corridor,
        "plans": plans,
        "results": results,
    }
    with open(RESULTS_FILE, "w") as f:
        json.dump(payload, f, indent=2)

    print_table(results)
    verdict(results)
    print(f"\nWrote {RESULTS_FILE}")


if __name__ == "__main__":
    main()
