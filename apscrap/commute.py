"""Load the travel-time grid written by scripts/commute_grid.py.

The grid is computed on a laptop because routing over the whole Lisbon transit
network needs several GB of memory, and the transit timetable only changes a
few times a year. The CSV starts with one metadata line:

    # label=Trabalho,step_lat=0.00225,step_lon=0.00285,departure=2026-11-03T08:00:00+00:00

followed by lat_idx,lon_idx,minutes rows. The file encodes where you work, so
it stays out of git (.gitignore).
"""
import csv
import sys
from pathlib import Path

from .db import connect


def _meta(first_line: str) -> dict:
    if not first_line.startswith("#"):
        raise ValueError("first line must be the '# label=...' metadata line")
    return dict(kv.split("=", 1) for kv in first_line[1:].strip().split(","))


def load(path: str) -> dict:
    lines = (sys.stdin if path == "-" else Path(path).open()).read().splitlines()
    meta = _meta(lines[0])
    rows = [(int(a), int(b), int(m)) for a, b, m in csv.reader(lines[2:])]  # [1] is the header

    with connect() as conn:
        conn.execute("TRUNCATE commute_cells")
        with conn.cursor().copy("COPY commute_cells (lat_idx, lon_idx, minutes) FROM STDIN") as cp:
            for row in rows:
                cp.write_row(row)
        conn.execute(
            """INSERT INTO commute_meta (id, label, step_lat, step_lon, departure, loaded_at)
               VALUES (1, %(label)s, %(step_lat)s, %(step_lon)s, %(departure)s, now())
               ON CONFLICT (id) DO UPDATE SET
                   label = EXCLUDED.label, step_lat = EXCLUDED.step_lat,
                   step_lon = EXCLUDED.step_lon, departure = EXCLUDED.departure,
                   loaded_at = now()""",
            meta,
        )
        conn.commit()
    return {"cells": len(rows), **meta}
