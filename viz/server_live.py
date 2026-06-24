#!/usr/bin/env python3
"""Live Flask-SocketIO server for Calgary SUMO visualization."""
import copy
import json
import math
import os
import random
import statistics
import sys
import xml.etree.ElementTree as ET

from flask import Flask, jsonify, send_from_directory
from flask_socketio import SocketIO, emit

SUMO_HOME = os.environ.get("SUMO_HOME", "/usr/share/sumo")
sys.path.insert(0, os.path.join(SUMO_HOME, "tools"))
import traci

VIZ_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(VIZ_DIR)
NET_FILE = os.path.join(PROJECT_DIR, "calgary_downtown.net.xml")
ROUTE_FILE = os.path.join(PROJECT_DIR, "od", "calgary_od_extended_calibrated.rou.xml")
TOP_TLS_FILE = os.path.join(PROJECT_DIR, "output", "top_tls.json")
EDGES_FILE = os.path.join(VIZ_DIR, "edges.geojson")
OUTPUT_DIR = os.path.join(PROJECT_DIR, "output")

SIM_STEPS = 3600
SCALE = 0.3
YELLOW = 3
RANDOM_SEED = 42
EARTH_RADIUS_M = 6371000.0
VALID_PLANS = {"baseline", "green_wave_ns", "random_offsets"}

app = Flask(__name__, static_folder=".")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
sim_state = {"status": "idle", "plan": None, "step": 0}


@app.route("/")
def index():
    return send_from_directory(VIZ_DIR, "index.html")


@app.route("/geojson/<name>")
def geojson(name):
    safe = name.replace(".geojson", "") + ".geojson"
    path = os.path.join(VIZ_DIR, safe)
    if os.path.exists(path):
        return send_from_directory(VIZ_DIR, safe)
    return jsonify({"error": "not found"}), 404


@app.route("/api/metrics")
def metrics():
    metrics_file = os.path.join(VIZ_DIR, "metrics.json")
    if os.path.exists(metrics_file):
        with open(metrics_file) as f:
            return jsonify(json.load(f))
    return jsonify({"error": "no metrics"}), 404


@socketio.on("connect")
def handle_connect():
    emit("sim_status", sim_state)


@socketio.on("start_simulation")
def handle_start(data):
    if sim_state["status"] == "running":
        emit("sim_error", {"message": "Simulation is already running"})
        return

    plan_name = (data or {}).get("plan", "baseline")
    if plan_name not in VALID_PLANS:
        emit("sim_error", {"message": f"Unknown signal plan: {plan_name}"})
        return

    sim_state.update({"status": "running", "plan": plan_name, "step": 0})
    socketio.emit("sim_status", sim_state)
    socketio.start_background_task(run_traci_simulation, plan_name)


def load_top_edges(limit=50):
    with open(EDGES_FILE) as f:
        data = json.load(f)
    features = data.get("features", [])
    features.sort(key=lambda f: f.get("properties", {}).get("volume", 0), reverse=True)
    return [f["properties"]["id"] for f in features[:limit] if f.get("properties", {}).get("id")]


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
    tls_ids = [m["id"] for m in members]
    coords = {m["id"]: m["coordinates"] for m in members}
    return tls_ids, coords


def cumulative_distances(tls_ids, coords):
    distances = {tls_ids[0]: 0.0}
    total = 0.0
    for prev, cur in zip(tls_ids, tls_ids[1:]):
        total += haversine_m(coords[prev], coords[cur])
        distances[cur] = total
    return distances


def build_plan(plan_name, tls_ids, coords):
    if plan_name == "baseline":
        return {"name": "baseline", "cycle": None, "offsets": {}}

    distances = cumulative_distances(tls_ids, coords)
    if plan_name == "green_wave_ns":
        offsets = {tls_id: distances[tls_id] / (50 / 3.6) for tls_id in tls_ids}
    else:
        rng = random.Random(RANDOM_SEED)
        offsets = {tls_id: rng.uniform(0, 75) for tls_id in tls_ids}

    return {
        "name": plan_name,
        "cycle": 75,
        "ns_green": 45,
        "ew_green": 24,
        "yellow": YELLOW,
        "offsets": offsets,
    }


def phase_at_offset(phases, offset):
    cycle = sum(float(p.duration) for p in phases)
    offset = offset % cycle
    elapsed = 0.0
    for index, phase in enumerate(phases):
        elapsed += float(phase.duration)
        if offset < elapsed:
            return index
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
        for phase in phases[4:]:
            state = phase.state.lower()
            if "y" in state and "g" not in state:
                phase.duration = plan["yellow"]

    phase_index = phase_at_offset(phases, plan["offsets"].get(tls_id, 0.0))
    logic = traci.trafficlight.Logic(prog.programID, prog.type, phase_index, phases)
    traci.trafficlight.setProgramLogic(tls_id, logic)
    traci.trafficlight.setPhase(tls_id, phase_index)
    return True, f"{len(phases)} phases"


def parse_tripinfo(path):
    if not os.path.exists(path):
        return {"n_trips": 0, "n_completed": 0}

    durations = []
    waits = []
    time_losses = []
    for _, elem in ET.iterparse(path, events=("end",)):
        if elem.tag == "tripinfo":
            durations.append(float(elem.get("duration", 0)))
            waits.append(float(elem.get("waitingTime", 0)))
            time_losses.append(float(elem.get("timeLoss", 0)))
            elem.clear()

    return {
        "n_trips": len(durations),
        "n_completed": len(durations),
        "avg_duration": statistics.mean(durations) if durations else 0,
        "avg_wait": statistics.mean(waits) if waits else 0,
        "avg_time_loss": statistics.mean(time_losses) if time_losses else 0,
        "total_wait": sum(waits),
    }


def collect_payload(step, edge_ids, tls_ids):
    edges = {}
    for edge_id in edge_ids:
        try:
            edges[edge_id] = traci.edge.getLastStepVehicleNumber(edge_id)
        except traci.TraCIException:
            edges[edge_id] = 0

    tls = {}
    for tls_id in tls_ids:
        try:
            tls[tls_id] = traci.trafficlight.getPhase(tls_id)
        except traci.TraCIException:
            tls[tls_id] = None

    vehicle_ids = traci.vehicle.getIDList()
    speeds = []
    for vehicle_id in vehicle_ids:
        try:
            speeds.append(traci.vehicle.getSpeed(vehicle_id))
        except traci.TraCIException:
            pass

    return {
        "step": step,
        "edges": edges,
        "tls": tls,
        "metrics": {
            "completed": traci.simulation.getArrivedNumber(),
            "mean_speed": statistics.mean(speeds) if speeds else 0,
            "total_vehicles": len(vehicle_ids),
        },
    }


def run_traci_simulation(plan_name="baseline"):
    edge_ids = load_top_edges()
    tls_ids, coords = load_corridor()
    plan = build_plan(plan_name, tls_ids, coords)
    tripinfo_file = os.path.join(OUTPUT_DIR, f"tripinfo_live_{plan_name}.xml")

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

    applied = {}
    warnings = []
    try:
        print(f"Starting live TraCI simulation: {plan_name}", flush=True)
        traci.start(sumo_cmd)

        available_tls = set(traci.trafficlight.getIDList())
        for tls_id in tls_ids:
            if tls_id not in available_tls:
                warnings.append(f"{tls_id}: missing from TraCI")
                applied[tls_id] = False
                continue
            if plan_name == "baseline":
                applied[tls_id] = True
            else:
                ok, note = apply_plan_to_tls(tls_id, plan)
                applied[tls_id] = ok
                if not ok:
                    warnings.append(f"{tls_id}: skipped ({note})")

        for step in range(1, SIM_STEPS + 1):
            traci.simulationStep()
            if step % 10 == 0:
                sim_state["step"] = step
                socketio.emit("sim_update", collect_payload(step, edge_ids, tls_ids))

        final_metrics = parse_tripinfo(tripinfo_file)
        final_metrics["applied_tls"] = sum(1 for ok in applied.values() if ok)
        final_metrics["skipped_tls"] = [tls_id for tls_id, ok in applied.items() if not ok]
        final_metrics["warnings"] = warnings
        sim_state.update({"status": "completed", "plan": plan_name, "step": SIM_STEPS})
        socketio.emit("sim_end", {"status": "completed", "plan": plan_name, "metrics": final_metrics})
        socketio.emit("sim_status", sim_state)
        print(f"Completed live TraCI simulation: {plan_name}", flush=True)
    except Exception as exc:
        sim_state.update({"status": "idle", "plan": None, "step": 0})
        socketio.emit("sim_error", {"message": str(exc)})
        socketio.emit("sim_status", sim_state)
        print(f"Live TraCI simulation failed: {exc}", flush=True)
    finally:
        try:
            traci.close()
        except Exception:
            pass


if __name__ == "__main__":
    print("Calgary SUMO Live Visualization Server")
    print(f"  Project: {PROJECT_DIR}")
    print("  Open: http://localhost:5001")
    socketio.run(app, host="0.0.0.0", port=5001, debug=False, allow_unsafe_werkzeug=True)
