#!/usr/bin/env python3
"""
Gymnasium environment for Calgary downtown corridor signal optimization.

Bandit formulation:
  - Action: choose 1 of 7 fixed signal plans (applied at episode start)
  - Observation: 50 edge densities + 16 TLS phase indices = 66 dims
  - Reward: mean_speed of all active vehicles (dense, per env-step)
  - Episode: 3600 sim seconds, 360 env-steps (delta_time=10s)
"""
import copy
import json
import math
import os
import random
import statistics
import sys

import gymnasium as gym
import numpy as np
from gymnasium import spaces

SUMO_HOME = os.environ.get("SUMO_HOME", "/usr/share/sumo")
sys.path.insert(0, os.path.join(SUMO_HOME, "tools"))
import traci

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
NET_FILE = os.path.join(PROJECT_DIR, "calgary_downtown.net.xml")
ROUTE_FILE = os.path.join(PROJECT_DIR, "od", "calgary_od_extended_calibrated.rou.xml")
TOP_TLS_FILE = os.path.join(PROJECT_DIR, "output", "top_tls.json")
EDGES_FILE = os.path.join(PROJECT_DIR, "viz", "edges.geojson")

SIM_DURATION = 3600
DELTA_TIME = 10        # sim seconds per env-step
STEP_LENGTH = 1        # 1s per sim step
SCALE = 0.3
YELLOW = 3
RANDOM_SEED = 42
EARTH_RADIUS_M = 6371000.0
MAX_EDGE_VEHICLES = 50.0   # normalization factor
MAX_TLS_PHASES = 8.0       # normalization factor

PLAN_NAMES = [
    "baseline",
    "uniform_short",
    "uniform_long",
    "green_wave_ns",
    "green_wave_ew",
    "green_wave_ns_fast",
    "random_offsets",
]


# ── Geometry helpers ──────────────────────────────────────────────────────

def _haversine_m(a, b):
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    x = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(x))


def _cumulative_distances(tls_ids, coords):
    distances = {tls_ids[0]: 0.0}
    total = 0.0
    for prev, cur in zip(tls_ids, tls_ids[1:]):
        total += _haversine_m(coords[prev], coords[cur])
        distances[cur] = total
    return distances


# ── Plan builder ──────────────────────────────────────────────────────────

def build_plan(name, tls_ids, coords):
    """Build a signal plan dict matching traci_corridor.py format."""
    if name == "baseline":
        return {"name": "baseline", "cycle": None, "ns_green": None,
                "ew_green": None, "yellow": None, "offsets": {}}

    distances = _cumulative_distances(tls_ids, coords)
    rng = random.Random(RANDOM_SEED)

    presets = {
        "uniform_short":      {"cycle": 60, "ns_green": 27, "ew_green": 27},
        "uniform_long":       {"cycle": 90, "ns_green": 42, "ew_green": 42},
        "green_wave_ns":      {"cycle": 75, "ns_green": 45, "ew_green": 24},
        "green_wave_ew":      {"cycle": 75, "ns_green": 24, "ew_green": 45},
        "green_wave_ns_fast": {"cycle": 75, "ns_green": 45, "ew_green": 24},
        "random_offsets":     {"cycle": 75, "ns_green": 45, "ew_green": 24},
    }

    p = presets[name]
    if "ns_fast" in name:
        offsets = {t: distances[t] / (60 / 3.6) for t in tls_ids}
    elif name.startswith("green_wave"):
        offsets = {t: distances[t] / 14.0 for t in tls_ids}
    elif name == "random_offsets":
        offsets = {t: rng.uniform(0, p["cycle"]) for t in tls_ids}
    else:
        offsets = {t: 0.0 for t in tls_ids}

    return {**p, "name": name, "yellow": YELLOW, "offsets": offsets}


# ── TLS application ───────────────────────────────────────────────────────

def _phase_at_offset(phases, offset):
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
        return False
    prog = logics[0]
    phases = list(copy.deepcopy(prog.getPhases()))
    if len(phases) < 4:
        return False

    phases[0].duration = plan["ns_green"]
    phases[1].duration = plan["yellow"]
    phases[2].duration = plan["ew_green"]
    phases[3].duration = plan["yellow"]
    if len(phases) > 4:
        for phase in phases[4:]:
            state = phase.state.lower()
            if "y" in state and "g" not in state:
                phase.duration = plan["yellow"]

    phase_index = _phase_at_offset(phases, plan["offsets"].get(tls_id, 0.0))
    logic = traci.trafficlight.Logic(prog.programID, prog.type, phase_index, phases)
    traci.trafficlight.setProgramLogic(tls_id, logic)
    traci.trafficlight.setPhase(tls_id, phase_index)
    return True


# ── Environment ───────────────────────────────────────────────────────────

class CalgaryCorridorEnv(gym.Env):
    """Bandit RL environment for Calgary downtown corridor signal plans."""

    metadata = {"render_modes": []}

    def __init__(self, scale=SCALE, sim_duration=SIM_DURATION,
                 delta_time=DELTA_TIME, step_length=STEP_LENGTH,
                 use_libsumo=False):
        super().__init__()
        self.scale = scale
        self.sim_duration = sim_duration
        self.delta_time = delta_time
        self.step_length = step_length
        self.n_env_steps = sim_duration // delta_time

        # Load static data
        self._load_static_data()

        # Action: choose 1 of 7 plans
        self.action_space = spaces.Discrete(len(PLAN_NAMES))

        # Observation: 50 edge densities + 16 TLS phases
        obs_dim = len(self.edge_ids) + len(self.tls_ids)
        self.observation_space = spaces.Box(
            low=0.0, high=1.0, shape=(obs_dim,), dtype=np.float32
        )

        # Runtime state
        self.current_step = 0
        self.plan_applied = False
        self.sumo_port = None

    def _load_static_data(self):
        # TLS corridor
        with open(TOP_TLS_FILE) as f:
            tls_data = json.load(f)
        members = tls_data["corridor"]["members"]
        self.tls_ids = [m["id"] for m in members]
        self.tls_coords = {m["id"]: m["coordinates"] for m in members}

        # Top 50 edges by volume
        with open(EDGES_FILE) as f:
            edges = json.load(f)
        features = sorted(
            edges["features"],
            key=lambda f: f["properties"].get("volume", 0),
            reverse=True,
        )
        self.edge_ids = [
            f["properties"]["id"] for f in features[:50]
            if f.get("properties", {}).get("id")
        ]

        # Pre-build plans
        self.plans = {
            name: build_plan(name, self.tls_ids, self.tls_coords)
            for name in PLAN_NAMES
        }

    def _make_sumo_cmd(self):
        label = f"calgary_env_{id(self)}"
        self.sumo_port = random.randint(10000, 60000)
        sumo_binary = os.path.join(SUMO_HOME, "bin", "sumo")
        return [
            sumo_binary,
            "-n", NET_FILE,
            "-r", ROUTE_FILE,
            "--begin", "0",
            "--end", str(self.sim_duration),
            "--step-length", str(self.step_length),
            "--scale", str(self.scale),
            "--time-to-teleport", "300",
            "--max-depart-delay", "600",
            "--no-step-log",
            "--no-warnings",
        ]

    def _get_obs(self):
        obs = np.zeros(len(self.edge_ids) + len(self.tls_ids), dtype=np.float32)

        # Edge densities
        for i, eid in enumerate(self.edge_ids):
            try:
                count = traci.edge.getLastStepVehicleNumber(eid)
                obs[i] = min(count / MAX_EDGE_VEHICLES, 1.0)
            except traci.TraCIException:
                obs[i] = 0.0

        # TLS phases
        offset = len(self.edge_ids)
        for i, tid in enumerate(self.tls_ids):
            try:
                phase = traci.trafficlight.getPhase(tid)
                obs[offset + i] = min(phase / MAX_TLS_PHASES, 1.0)
            except traci.TraCIException:
                obs[offset + i] = 0.0

        return obs

    def _get_reward(self):
        """Dense reward: mean speed of all active vehicles."""
        vids = traci.vehicle.getIDList()
        if not vids:
            return 0.0
        speeds = []
        for vid in vids:
            try:
                speeds.append(traci.vehicle.getSpeed(vid))
            except traci.TraCIException:
                pass
        # Normalize by max urban speed (~13.9 m/s = 50 km/h)
        mean_speed = statistics.mean(speeds) if speeds else 0.0
        return mean_speed / 13.9

    def _apply_plan(self, action):
        plan_name = PLAN_NAMES[action]
        plan = self.plans[plan_name]
        available = set(traci.trafficlight.getIDList())
        applied = 0
        for tid in self.tls_ids:
            if tid not in available:
                continue
            if plan_name == "baseline":
                applied += 1
            else:
                if apply_plan_to_tls(tid, plan):
                    applied += 1
        return applied

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)

        # Close any existing connection
        try:
            traci.close()
        except Exception:
            pass

        sumo_cmd = self._make_sumo_cmd()
        traci.start(sumo_cmd)

        self.current_step = 0
        self.plan_applied = False

        obs = self._get_obs()
        info = {"step": 0}
        return obs, info

    def step(self, action):
        # Apply plan only on first env-step
        if not self.plan_applied:
            self._apply_plan(int(action))
            self.plan_applied = True

        # Advance simulation by delta_time steps
        total_speed = 0.0
        for _ in range(self.delta_time):
            traci.simulationStep()
            self.current_step += 1
            total_speed += self._get_reward()

        # Average reward over the delta_time window
        reward = total_speed / self.delta_time

        obs = self._get_obs()
        terminated = self.current_step >= self.sim_duration
        truncated = False

        info = {
            "step": self.current_step,
            "plan": PLAN_NAMES[int(action)],
            "vehicles": len(traci.vehicle.getIDList()),
            "arrived": traci.simulation.getArrivedNumber(),
        }

        if terminated:
            try:
                traci.close()
            except Exception:
                pass

        return obs, reward, terminated, truncated, info

    def close(self):
        try:
            traci.close()
        except Exception:
            pass


# ── Self-test ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time

    print("=" * 60)
    print("  CalgaryCorridorEnv — Self-test")
    print("=" * 60)

    env = CalgaryCorridorEnv()

    print(f"Action space:  Discrete({env.action_space.n}) — {PLAN_NAMES}")
    print(f"Observation:   Box({env.observation_space.shape})")
    print(f"Edges:         {len(env.edge_ids)}")
    print(f"TLS:           {len(env.tls_ids)}")
    print(f"Episode:       {env.n_env_steps} env-steps × {env.delta_time}s = {env.sim_duration}s")
    print()

    # Test 1: reset
    print("[1/4] reset()...", end=" ", flush=True)
    t0 = time.monotonic()
    obs, info = env.reset()
    t_reset = time.monotonic() - t0
    assert obs.shape == env.observation_space.shape, f"Bad obs shape: {obs.shape}"
    assert np.all(obs >= 0) and np.all(obs <= 1), "Obs out of [0,1] range"
    print(f"OK ({t_reset:.2f}s) | obs range [{obs.min():.3f}, {obs.max():.3f}]")

    # Test 2: step with action 0 (baseline)
    print("[2/4] step(0) baseline...", end=" ", flush=True)
    t0 = time.monotonic()
    obs, reward, term, trunc, info = env.step(0)
    t_step = time.monotonic() - t0
    print(f"OK ({t_step:.3f}s) | reward={reward:.4f} | veh={info['vehicles']} | plan={info['plan']}")

    # Test 3: run 10 more steps
    print("[3/4] 10 more steps...", end=" ", flush=True)
    rewards = [reward]
    for i in range(10):
        obs, r, term, trunc, info = env.step(0)
        rewards.append(r)
    print(f"OK | mean_reward={np.mean(rewards):.4f} | step={info['step']}")

    # Test 4: test a different plan
    env.close()
    print("[4/4] Full episode with green_wave_ns (action=3)...", end=" ", flush=True)
    obs, info = env.reset()
    t0 = time.monotonic()
    ep_reward = 0.0
    n_steps = 0
    while True:
        obs, r, term, trunc, info = env.step(3)
        ep_reward += r
        n_steps += 1
        if term:
            break
    t_ep = time.monotonic() - t0
    print(f"OK ({t_ep:.1f}s)")
    print(f"     Steps: {n_steps} | Total reward: {ep_reward:.2f} | "
          f"Avg reward/step: {ep_reward/n_steps:.4f}")

    # Timing summary
    print()
    print("=" * 60)
    print("  TIMING SUMMARY")
    print("=" * 60)
    print(f"  reset():          {t_reset:.2f}s")
    print(f"  step() avg:       {t_ep/n_steps*1000:.1f}ms")
    print(f"  Full episode:     {t_ep:.1f}s ({n_steps} env-steps)")
    print(f"  Episodes/hour:    {3600/t_ep:.0f}")
    print(f"  10k PPO steps:    {10000 * t_ep/n_steps / 60:.1f} min")
    print()

    env.close()
    print("All tests passed ✓")
