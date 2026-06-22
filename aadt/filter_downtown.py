#!/usr/bin/env python3
"""Filter AADT records to downtown Calgary bbox and extract matching points."""
import json, sys

data = json.load(open('aadt/traffic_volumes_2023.json'))
print(f"Total records: {len(data)}")

# Downtown bbox: 51.03-51.08N, -114.10 to -114.04W
downtown = []
for r in data:
    coords = r.get('multilinestring', {}).get('coordinates', [])
    if not coords:
        continue
    flat = [c for line in coords for c in line]
    lats = [c[1] for c in flat]
    lons = [c[0] for c in flat]
    midlat = sum(lats) / len(lats)
    midlon = sum(lons) / len(lons)
    if 51.03 <= midlat <= 51.08 and -114.10 <= midlon <= -114.04:
        r['_midlat'] = round(midlat, 7)
        r['_midlon'] = round(midlon, 7)
        downtown.append(r)

print(f"Downtown records: {len(downtown)}")
vols = [float(r['volume']) for r in downtown]
if vols:
    print(f"Volume range: {min(vols):.0f} - {max(vols):.0f} (mean {sum(vols)/len(vols):.0f})")

# Save filtered set (just the fields we need for matching)
out = []
for r in downtown:
    out.append({
        'section_name': r.get('section_name', ''),
        'collection': r.get('collection', ''),
        'volume': float(r['volume']),
        'midlat': r['_midlat'],
        'midlon': r['_midlon'],
    })
json.dump(out, open('aadt/downtown_aadt.json', 'w'), indent=2)
print(f"Saved {len(out)} filtered records to aadt/downtown_aadt.json")
