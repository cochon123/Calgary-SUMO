#!/usr/bin/env bash
# =============================================================================
# Download the Calgary OSM extract. Two methods; pick whichever works.
# Run from the project root:  ./get_osm.sh
# =============================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p osm

Q=osm/calgary_query.overpassql
OUT=osm/calgary.osm.gz

echo "=== Method A: Overpass (City of Calgary boundary, highways+railways) ==="
if curl -sf --max-time 600 -H "User-Agent: CalgarySUMO/1.0" \
     --data-urlencode "data@$Q" \
     https://overpass-api.de/api/interpreter \
     | gzip > "$OUT" && [[ -s "$OUT" ]]; then
  echo "Overpass OK -> $OUT ($(du -h "$OUT" | cut -f1))"
else
  echo "Overpass failed/empty. Trying BBBike mirror..."
  # Method B: BBBike ready-made Calgary extract (.osm.gz)
  BBB="https://download.bbbike.org/osm/bbbike/Calgary/Calgary.osm.gz"
  if curl -fL --max-time 600 -H "User-Agent: CalgarySUMO/1.0" "$BBB" -o "$OUT" \
     && [[ -s "$OUT" ]]; then
    echo "BBBike OK -> $OUT ($(du -h "$OUT" | cut -f1))"
  else
    echo "FAILED: could not download Calgary OSM from either source." >&2
    echo "Download manually from https://download.bbbike.org/osm/bbbike/Calgary/" >&2
    exit 1
  fi
fi

echo ""
echo "Next: ensure SUMO is installed and SUMO_HOME is set, then run ./convert.sh"
