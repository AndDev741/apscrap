#!/usr/bin/env python3
"""Cut Greater Lisbon out of the Geofabrik Portugal extract.

R5 refuses a street network wider than 975,000 km2, and portugal-latest
includes the Azores and Madeira. Takes about six minutes; needs pyosmium
(pip install osmium).

    commute_clip_osm.py portugal-latest.osm.pbf lisboa.osm.pbf
"""
import sys

import osmium

S, W, N, E = 38.55, -9.55, 39.05, -8.85   # a margin around commute_grid.BBOX


def inside(loc) -> bool:
    return loc.valid() and S <= loc.lat <= N and W <= loc.lon <= E


def main() -> None:
    src, dst = sys.argv[1], sys.argv[2]
    # BackReferenceWriter adds the nodes every kept way needs, so ways that
    # cross the edge stay whole.
    with osmium.BackReferenceWriter(dst, ref_src=src, overwrite=True) as out:
        for obj in osmium.FileProcessor(src).with_locations():
            if obj.is_node() and inside(obj.location):
                out.add_node(obj)
            elif obj.is_way() and any(inside(nd.location) for nd in obj.nodes):
                out.add_way(obj)


if __name__ == "__main__":
    main()
