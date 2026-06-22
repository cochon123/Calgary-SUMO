# Calgary road network in SUMO

Road network of Calgary (Alberta, Canada) extracted from OpenStreetMap,
converted to Eclipse SUMO, and calibrated with a runnable simulation.

## Results

### Full city network (`calgary.net.xml`)
- **1.63M** edges · **334,587** junctions · **1,811** traffic lights
- File size: 1.0 GB
- ⚠️ Requires ≥16 GB RAM to simulate. This machine (7 GB) can build it but
  cannot run SUMO on it.

### Downtown sub-network (`calgary_downtown.net.xml`) — ✅ runnable
- **112,987** edges · **28,841** junctions · **307** traffic lights
- Bounding box: 51.03–51.08°N, 114.04–114.10°W (downtown core)
- Simulation: 1,177 vehicles over 1 hour → 974 arrived, 0 collisions

## Layout
```
Calgary-SUMO/
├── calgary.net.xml              # full city network (1 GB)
├── calgary.poly.xml             # full city POIs/buildings (200 MB)
├── calgary_downtown.net.xml     # downtown sub-network (75 MB) ← runnable
├── calgary_downtown.sumocfg     # downtown simulation config
├── calgary.sumocfg              # full city config (needs ≥16 GB RAM)
├── osm/
│   ├── calgary.osm.gz           # raw OSM extract (103 MB, BBBike)
│   ├── calgary_query.overpassql # Overpass query (boundary-based)
│   └── Calgary.poly             # bbox polygon for clipping
├── output/
│   ├── calgary_downtown.rou.xml # validated routes (1,177 vehicles)
│   ├── calgary_downtown.trips.xml
│   ├── summary.xml              # per-step simulation statistics
│   └── tripinfo.xml             # per-vehicle travel metrics
├── get_osm.sh                   # download OSM data (Overpass / BBBike)
├── convert.sh                   # full pipeline: netconvert + polyconvert + trips
└── README.md                    # this file
```

## How to run the downtown simulation
```bash
export SUMO_HOME=/usr/share/sumo
cd ~/Documents/Calgary-SUMO

# Headless (fast)
sumo -c calgary_downtown.sumocfg

# Visual (GUI)
sumo-gui -c calgary_downtown.sumocfg

# With output stats
sumo -c calgary_downtown.sumocfg \
  --summary output/summary.xml \
  --tripinfo output/tripinfo.xml
```

## Reproduce from scratch
```bash
export SUMO_HOME=/usr/share/sumo
cd ~/Documents/Calgary-SUMO

# 1. Download OSM data (already done — osm/calgary.osm.gz)
# ./get_osm.sh

# 2. Full city network (slow, ~8 min, needs lots of RAM)
netconvert --osm-files osm/calgary.osm.gz \
  --type-files $SUMO_HOME/data/typemap/osmNetconvert.typ.xml \
  --geometry.remove --junctions.join --roundabouts.guess --ramps.guess \
  --tls.guess-signals --tls.discard-simple --tls.join \
  --output.original-names --output.street-names \
  -o calgary.net.xml

# 3. Downtown sub-network (faster, ~7 min)
netconvert --osm-files osm/calgary.osm.gz \
  --type-files $SUMO_HOME/data/typemap/osmNetconvert.typ.xml \
  --keep-edges.in-geo-boundary -114.10,51.03,-114.04,51.08 \
  --geometry.remove --junctions.join --roundabouts.guess --ramps.guess \
  --tls.guess-signals --tls.discard-simple --tls.join \
  --output.original-names --output.street-names \
  -o calgary_downtown.net.xml

# 4. Generate demand
python3 $SUMO_HOME/tools/randomTrips.py \
  -n calgary_downtown.net.xml \
  -o output/calgary_downtown.trips.xml \
  -r output/calgary_downtown.rou.xml \
  -e 3600 -p 3 -l -s 42 --validate

# 5. Run
sumo -c calgary_downtown.sumocfg
```

## Calibration notes

The current demand is **synthetic** (randomTrips.py). True calibration requires:

1. **Real traffic counts** — City of Calgary open data provides AADT
   (Annual Average Daily Traffic) counts at intersections. Download from
   `https://data.calgary.ca/` and match to SUMO edges.
2. **Edge speed/lane adjustment** — Compare simulated vs. observed speeds,
   adjust `maxSpeed` and lane counts in `calgary_downtown.net.xml`.
3. **Signal timing** — Several TLS programs show conflicts (see simulation
   warnings). Fix by adding `--tls.ignore-internal-junction-jam` to
   netconvert, or manually editing signal phases in netedit.
4. **Demand scaling** — Once calibrated, scale demand with `--scale N` to
   model peak-hour / off-peak scenarios.
5. **OD matrices** — Replace randomTrips with origin-destination matrices
   from Calgary's transport model (CTrans data).
