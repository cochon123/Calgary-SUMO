#!/usr/bin/env bash
# =============================================================================
# Calgary OSM -> SUMO network conversion + basic calibration setup
# Requires: SUMO installed (sumo, netconvert, polyconvert) and SUMO_HOME set.
# Data:     osm/calgary.osm.gz  (Calgary OSM extract)
# =============================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

# --- locate SUMO type maps (shipped with every SUMO install) -----------------
if [[ -z "${SUMO_HOME:-}" ]]; then
  echo "ERROR: SUMO_HOME is not set. Export it, e.g.:" >&2
  echo "  export SUMO_HOME=/usr/share/sumo      # apt install" >&2
  echo "  export SUMO_HOME=\$HOME/.local/micromamba/envs/sumo/share/sumo  # conda" >&2
  exit 1
fi
TYPENET="$SUMO_HOME/data/typemap/osmNetconvert.typ.xml"
TYPEPOLY="$SUMO_HOME/data/typemap/osmPolyconvert.typ.xml"

OSM=osm/calgary.osm.gz
[[ -f "$OSM" ]] || { echo "ERROR: $OSM not found. Put the Calgary OSM extract there first." >&2; exit 1; }

echo "==[1/4] netconvert: OSM -> road network (calgary.net.xml) ============="
netconvert \
  --osm-files "$OSM" \
  --type-files "$TYPENET" \
  --geometry.remove --rectangular-lane-cut true --junctions.join \
  --roundabouts.guess --ramps.guess \
  --tls.guess-signals --tls.discard-simple --tls.join \
  --output.original-names --output.street-names \
  -o calgary.net.xml

echo "==[2/4] polyconvert: OSM POIs/polygons (calgary.poly.xml) ============"
polyconvert \
  --net-file calgary.net.xml \
  --osm-files "$OSM" \
  --type-file "$TYPEPOLY" \
  -o calgary.poly.xml

echo "==[3/4] random demand (first-pass calibration seed) =================="
# Synthetic traffic to make the network runnable; replace with real counts later.
python3 "$SUMO_HOME/tools/randomTrips.py" \
  -n calgary.net.xml \
  -o output/calgary.trips.xml \
  -r output/calgary.rou.xml \
  -e 1800 -p 5 -l -s 42

echo "==[4/4] write sumocfg ================================================="
cat > calgary.sumocfg <<'CFGEOF'
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <input>
    <net-file value="calgary.net.xml"/>
    <route-files value="output/calgary.rou.xml"/>
    <additional-files value="calgary.poly.xml"/>
  </input>
  <time>
    <begin value="0"/>
    <end value="1800"/>
  </time>
  <processing>
    <time-to-teleport value="-1"/>
  </processing>
</configuration>
CFGEOF

echo ""
echo "DONE. Run the simulation with:"
echo "  sumo -c calgary.sumocfg            # headless"
echo "  sumo-gui -c calgary.sumocfg        # visual (needs FOX/GUI build)"
echo ""
echo "Calibration hint: adjust edge speeds / lane counts against real AADT"
echo "counts (City of Calgary open data) and re-run with --scale."
