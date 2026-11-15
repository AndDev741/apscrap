"""Load a travel-time grid written by scripts/commute_grid.py (IM-6).

The grid is computed on a laptop because routing over the whole Lisbon transit
network needs several GB of memory, and the transit timetable only changes a
few times a year. The CSV starts with one metadata line:

    # label=Trabalho,mode=transit,step_lat=0.00225,step_lon=0.00285,departure=2026-11-03T08:00:00+00:00

followed by a header and lat_idx,lon_idx,minutes rows. Each grid belongs to
one person (slot a or b) and one mode (transit or car). The file encodes where
someone works, so it stays out of git (.gitignore).

    apscrap commute-load grid.csv --slot b                 # mode from the file
    apscrap commute-load grid.csv --slot a --lat .. --lon ..  # also store the point
"""
import csv
import sys
from pathlib import Path

from .db import connect


def _meta(first_line: str) -> dict:
    if not first_line.startswith("#"):
        raise ValueError("first line must be the '# label=...' metadata line")
    return dict(kv.split("=", 1) for kv in first_line[1:].strip().split(","))


def load(path: str, slot: str = "a", mode: str | None = None,
         lat: float | None = None, lon: float | None = None) -> dict:
    lines = (sys.stdin if path == "-" else Path(path).open()).read().splitlines()
    meta = _meta(lines[0])
    mode = mode or meta.get("mode") or "transit"
    rows = [(int(a), int(b), int(m)) for a, b, m in csv.reader(lines[2:])]  # [1] is the header

    with connect() as conn:
        dest = conn.execute(
            """INSERT INTO commute_destinations (slot, mode, label, lat, lon, step_lat, step_lon,
                                                 departure, loaded_at, stale)
               VALUES (%(slot)s, %(mode)s, %(label)s, %(lat)s, %(lon)s, %(step_lat)s, %(step_lon)s,
                       %(departure)s, now(), false)
               ON CONFLICT (slot, mode) DO UPDATE SET
                   label = EXCLUDED.label, step_lat = EXCLUDED.step_lat, step_lon = EXCLUDED.step_lon,
                   departure = EXCLUDED.departure, loaded_at = now(), stale = false,
                   lat = COALESCE(EXCLUDED.lat, commute_destinations.lat),
                   lon = COALESCE(EXCLUDED.lon, commute_destinations.lon)
               RETURNING id""",
            {"slot": slot, "mode": mode, "label": meta["label"], "lat": lat, "lon": lon,
             "step_lat": meta["step_lat"], "step_lon": meta["step_lon"], "departure": meta["departure"]},
        ).fetchone()["id"]
        conn.execute("DELETE FROM commute_cells WHERE dest_id = %s", (dest,))
        with conn.cursor().copy("COPY commute_cells (dest_id, lat_idx, lon_idx, minutes) FROM STDIN") as cp:
            for row in rows:
                cp.write_row((dest, *row))
        if dest == 1:
            # the single-grid table some older queries still read
            conn.execute(
                """INSERT INTO commute_meta (id, label, step_lat, step_lon, departure, loaded_at)
                   VALUES (1, %(label)s, %(step_lat)s, %(step_lon)s, %(departure)s, now())
                   ON CONFLICT (id) DO UPDATE SET label = EXCLUDED.label, step_lat = EXCLUDED.step_lat,
                       step_lon = EXCLUDED.step_lon, departure = EXCLUDED.departure, loaded_at = now()""",
                meta)
        conn.commit()
    return {"dest_id": dest, "slot": slot, "mode": mode, "cells": len(rows), "label": meta["label"]}
