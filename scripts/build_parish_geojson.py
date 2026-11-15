#!/usr/bin/env python3
"""Parish outlines for the app's geographic zone map (IM-1).

Fetches the official CAOP outlines of every parish in the Grande Lisboa
councils from geoapi.pt (one request per council), simplifies them to about
40 m, rounds coordinates to 5 decimals and writes site/geo/freguesias.geojson
with the parish named the way the listings name it ("União das freguesias
de X e Y" becomes "X e Y"). Boundaries change only when CAOP does, so run it
again once a year or when a council is added to the criteria.

    pip install shapely
    python scripts/build_parish_geojson.py
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from shapely.geometry import mapping, shape

COUNCILS = ["Lisboa", "Odivelas", "Loures", "Amadora", "Oeiras", "Sintra", "Cascais",
            "Vila Franca de Xira", "Mafra"]
OUT = Path(__file__).resolve().parent.parent / "site" / "geo" / "freguesias.geojson"
TOLERANCE = 0.0004   # degrees, roughly 40 m


def rounded(geom):
    def r(coords):
        if isinstance(coords[0], (int, float)):
            return [round(coords[0], 5), round(coords[1], 5)]
        return [r(c) for c in coords]
    g = mapping(geom)
    return {"type": g["type"], "coordinates": r(g["coordinates"])}


def main():
    features = []
    for council in COUNCILS:
        url = "https://json.geoapi.pt/municipio/" + urllib.parse.quote(council) + "/freguesias"
        req = urllib.request.Request(url, headers={"User-Agent": "apscrap (parish outlines, yearly)"})
        data = json.load(urllib.request.urlopen(req, timeout=60))
        for f in data["geojsons"]["freguesias"]:
            p = f["properties"]
            name = p["freguesia"].replace("União das freguesias de ", "").strip()
            geom = shape(f["geometry"]).simplify(TOLERANCE, preserve_topology=True)
            features.append({"type": "Feature",
                             "properties": {"council": council, "parish": name, "dicofre": p.get("dtmnfr")},
                             "geometry": rounded(geom)})
        print(council, len(data["geojsons"]["freguesias"]))
        time.sleep(1)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                              ensure_ascii=False, separators=(",", ":")))
    print(f"{len(features)} parishes, {OUT.stat().st_size // 1024} KiB -> {OUT}")


if __name__ == "__main__":
    main()
