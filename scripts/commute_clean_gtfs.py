#!/usr/bin/env python3
"""Drop trips whose service_id is in neither calendar.txt nor calendar_dates.txt.

R5 refuses a whole feed over one such trip. Carris Metropolitana ships tens of
thousands of them (October 2026). Their stop_times go too.

    commute_clean_gtfs.py carris_metropolitana.zip carris_metropolitana_clean.zip
"""
import csv
import io
import sys
import zipfile


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    zin = zipfile.ZipFile(src)
    names = set(zin.namelist())

    def rows(name):
        with zin.open(name) as f:
            yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))

    services = set()
    for name in ("calendar.txt", "calendar_dates.txt"):
        if name in names:
            services |= {r["service_id"] for r in rows(name)}
    bad = {r["trip_id"] for r in rows("trips.txt") if r["service_id"] not in services}
    print(f"{src}: dropping {len(bad)} trips with an unknown service_id")

    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            if name not in ("trips.txt", "stop_times.txt"):
                zout.writestr(name, zin.read(name))
                continue
            with zin.open(name) as f:
                reader = csv.reader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                header = next(reader)
                idx = header.index("trip_id")
                buf = io.StringIO()
                writer = csv.writer(buf, lineterminator="\n")
                writer.writerow(header)
                writer.writerows(r for r in reader if r[idx] not in bad)
                zout.writestr(name, buf.getvalue())


if __name__ == "__main__":
    main()
