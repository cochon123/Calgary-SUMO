#!/usr/bin/env python3
"""Benchmark episode time at different step-lengths to pick the right one for PPO."""
import os
import sys
import time
import statistics

SUMO_HOME = os.environ.get("SUMO_HOME", "/usr/share/sumo")
sys.path.insert(0, os.path.join(SUMO_HOME, "tools"))
import traci

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
NET_FILE = os.path.join(PROJECT_DIR, "calgary_downtown.net.xml")
ROUTE_FILE = os.path.join(PROJECT_DIR, "od", "calgary_od_extended_calibrated.rou.xml")
SCALE = 0.3
SIM_DURATION = 3600  # seconds of simulated time


def run_episode(step_length, label):
    """Run a single SUMO episode and return wall-clock time."""
    n_steps = int(SIM_DURATION / step_length)
    sumo_cmd = [
        os.path.join(SUMO_HOME, "bin", "sumo"),
        "-n", NET_FILE,
        "-r", ROUTE_FILE,
        "--begin", "0",
        "--end", str(SIM_DURATION),
        "--step-length", str(step_length),
        "--scale", str(SCALE),
        "--time-to-teleport", "300",
        "--max-depart-delay", "600",
        "--no-step-log",
        "--no-warnings",
    ]
    traci.start(sumo_cmd)
    step_times = []
    t0 = time.monotonic()
    for step in range(n_steps):
        ts = time.monotonic()
        traci.simulationStep()
        if step < 100:  # sample first 100 steps
            step_times.append(time.monotonic() - ts)
    total = time.monotonic() - t0
    vehicles = len(traci.vehicle.getIDList())
    arrived = traci.simulation.getArrivedNumber()
    traci.close()

    avg_step_ms = statistics.mean(step_times) * 1000 if step_times else 0
    print(f"  {label}: {total:.1f}s wall | {n_steps} steps | "
          f"avg step: {avg_step_ms:.1f}ms | "
          f"final veh: {vehicles} | arrived: {arrived}")
    return total, n_steps, avg_step_ms


def main():
    print("=" * 72)
    print("  EPISODE TIME BENCHMARK")
    print("=" * 72)
    print(f"Network: calgary_downtown.net.xml")
    print(f"Routes: calgary_od_extended_calibrated.rou.xml")
    print(f"Scale: {SCALE} | Sim duration: {SIM_DURATION}s")
    print()

    configs = [
        (1, "step-length=1 (3600 steps)"),
        (5, "step-length=5 (720 steps)"),
        (10, "step-length=10 (360 steps)"),
    ]

    results = {}
    for step_length, label in configs:
        total, n_steps, avg_step_ms = run_episode(step_length, label)
        results[step_length] = {
            "wall_time": total,
            "n_steps": n_steps,
            "avg_step_ms": avg_step_ms,
        }

    print()
    print("=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    baseline_time = results[1]["wall_time"]
    for step_length, label in configs:
        r = results[step_length]
        speedup = baseline_time / r["wall_time"] if r["wall_time"] > 0 else 0
        episodes_per_hour = 3600 / r["wall_time"] if r["wall_time"] > 0 else 0
        print(f"  step-length={step_length:>2}: {r['wall_time']:>6.1f}s/episode | "
              f"{episodes_per_hour:>5.0f} ep/hr | "
              f"{speedup:>4.1f}x faster | "
              f"{r['avg_step_ms']:>5.1f}ms/step")
    print()
    print("Recommendation for PPO training:")
    best = min(results, key=lambda s: results[s]["wall_time"])
    print(f"  Use step-length={best} for fastest iteration.")
    print(f"  At step-length=10: 1000 episodes ≈ {results.get(10, results[best])['wall_time'] * 1000 / 3600:.1f} hours")


if __name__ == "__main__":
    main()
