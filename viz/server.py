#!/usr/bin/env python3
"""
Flask server for Calgary SUMO visualization.

Serves static GeoJSON data and exposes API endpoints.
Designed as a separation layer: static data now, WebSocket-ready later.

Run: python3 viz/server.py
Open: http://localhost:5000
"""
import json
import os
import subprocess
import sys
from flask import Flask, jsonify, send_from_directory, request

app = Flask(__name__, static_folder='.')

VIZ_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(VIZ_DIR)


@app.route('/')
def index():
    return send_from_directory(VIZ_DIR, 'index.html')


@app.route('/geojson/<name>')
def geojson(name):
    """Serve GeoJSON files."""
    safe = name.replace('.geojson', '') + '.geojson'
    path = os.path.join(VIZ_DIR, safe)
    if os.path.exists(path):
        return send_from_directory(VIZ_DIR, safe)
    return jsonify({"error": "not found"}), 404


@app.route('/api/metrics')
def metrics():
    """Return current calibration and simulation metrics."""
    metrics_file = os.path.join(VIZ_DIR, 'metrics.json')
    if os.path.exists(metrics_file):
        with open(metrics_file) as f:
            return jsonify(json.load(f))
    return jsonify({"error": "no metrics"}), 404


@app.route('/api/run-simulation', methods=['POST'])
def run_simulation():
    """Trigger a TraCI optimization run. (Stub for now — returns cached results.)"""
    # TODO: When WebSocket layer arrives, this will stream results live.
    # For now, return the last optimization results.
    results_file = os.path.join(PROJECT_DIR, 'output', 'traci_optimization_results.json')
    if os.path.exists(results_file):
        with open(results_file) as f:
            return jsonify({"status": "cached", "results": json.load(f)})
    return jsonify({"status": "no_results", "message": "Run traci_optimize.py first"}), 404


if __name__ == '__main__':
    print(f"Calgary SUMO Visualization Server")
    print(f"  Project: {PROJECT_DIR}")
    print(f"  Open: http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
