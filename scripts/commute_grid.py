#!/usr/bin/env python3
"""Public-transit minutes from a grid over Greater Lisbon to one destination.

Run on a laptop, not on the server: Conveyal R5 (through r5py) needs several
GB of memory to route over the metropolitan network. The result changes only
when timetables do, so re-run it a few times a year and when you change jobs.

    python3 -m venv /tmp/r5 && /tmp/r5/bin/pip install "r5py>=1.0" osmium

    # 1. street network: Geofabrik Portugal, cut down to Greater Lisbon
    curl -LO https://download.geofabrik.de/europe/portugal-latest.osm.pbf
    /tmp/r5/bin/python scripts/commute_clip_osm.py portugal-latest.osm.pbf lisboa.osm.pbf

    # 2. timetables, one file per operator, each with its own name: r5py caches
    #    converted feeds by file name, so seven files called gtfs.zip collide
    curl -L -o metro_lisboa.zip https://www.metrolisboa.pt/google_transit/googleTransit.zip
    curl -L -o carris.zip https://gateway.carris.pt/gateway/gtfs/api/v2.11/GTFS
    curl -L -o cm_raw.zip https://api.carrismetropolitana.pt/gtfs
    curl -L -o cp.zip https://publico.cp.pt/gtfs/gtfs.zip
    curl -L -o fertagus.zip https://www.fertagus.pt/GTFSTMLzip/Fertagus_GTFS.zip
    curl -L -o transtejo.zip https://api.ttsl.pt/files/GTFS.zip
    curl -L -o mts.zip https://files.mobilitydatabase.org/mdb-2408/latest.zip
    /tmp/r5/bin/python scripts/commute_clean_gtfs.py cm_raw.zip carris_metropolitana.zip

    # 3. the grid (a few minutes), then load it
    /tmp/r5/bin/python scripts/commute_grid.py \
        --lat <work lat> --lon <work lon> --label <name shown on the site> \
        --osm lisboa.osm.pbf --gtfs metro_lisboa.zip carris.zip carris_metropolitana.zip \
            cp.zip fertagus.zip transtejo.zip mts.zip \
        --out ~/apscrap.commute.csv
    apscrap commute-load ~/apscrap.commute.csv        # or DEPLOY.md for the server

TML's all-operators feed (go.tmlmobilidade.pt/hub/api/v1/plans/gtfs) looks
simpler but carries the same broken Carris Metropolitana trips, and mixing it
with the per-operator feeds counts every trip twice. If a run fails on a feed
error that is already fixed, clear ~/.cache/r5py.

The output encodes where you work. Keep it out of git; .gitignore covers
*.commute.csv and the default name.
"""
import argparse
import datetime as dt
import math

import geopandas as gpd
import r5py
from shapely.geometry import Point

# Lisboa, Odivelas, Loures, Amadora, Oeiras, Cascais, most of Sintra and
# Vila Franca, and the south bank. Listings outside get no commute figure.
BBOX = (38.60, -9.50, 39.00, -8.92)   # south, west, north, east
STEP_M = 300


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--osm", required=True)
    ap.add_argument("--gtfs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--departure", default="2026-11-03T08:00",
                    help="a weekday morning inside every feed's service dates")
    ap.add_argument("--window-min", type=int, default=60,
                    help="R5 reports the median over departures in this window")
    args = ap.parse_args()

    south, west, north, east = BBOX
    step_lat = STEP_M / 111_320
    step_lon = STEP_M / (111_320 * math.cos(math.radians((south + north) / 2)))

    # Cells are addressed by integer index = round(coordinate / step), the same
    # rounding v_listing_current uses to find a listing's cell.
    rows = []
    for i in range(round(south / step_lat), round(north / step_lat) + 1):
        for j in range(round(west / step_lon), round(east / step_lon) + 1):
            rows.append((f"{i}:{j}", i, j, Point(j * step_lon, i * step_lat)))
    origins = gpd.GeoDataFrame(
        {"id": [r[0] for r in rows], "lat_idx": [r[1] for r in rows], "lon_idx": [r[2] for r in rows]},
        geometry=[r[3] for r in rows], crs="EPSG:4326",
    )
    destination = gpd.GeoDataFrame({"id": ["work"]}, geometry=[Point(args.lon, args.lat)], crs="EPSG:4326")
    print(f"{len(origins)} grid points, {STEP_M} m apart")

    network = r5py.TransportNetwork(args.osm, args.gtfs)
    departure = dt.datetime.fromisoformat(args.departure)
    ttm = r5py.TravelTimeMatrix(
        network,
        origins=origins,
        destinations=destination,
        departure=departure,
        departure_time_window=dt.timedelta(minutes=args.window_min),
        transport_modes=[r5py.TransportMode.TRANSIT, r5py.TransportMode.WALK],
        max_time=dt.timedelta(minutes=120),
    )

    reached = ttm.dropna(subset=["travel_time"]).merge(origins[["id", "lat_idx", "lon_idx"]], left_on="from_id", right_on="id")
    with open(args.out, "w") as f:
        f.write(f"# label={args.label},step_lat={step_lat:.8f},step_lon={step_lon:.8f},"
                f"departure={departure.isoformat()}\n")
        f.write("lat_idx,lon_idx,minutes\n")
        for r in reached.itertuples():
            f.write(f"{r.lat_idx},{r.lon_idx},{int(r.travel_time)}\n")
    print(f"wrote {len(reached)} reachable cells of {len(origins)} to {args.out}")


if __name__ == "__main__":
    main()
