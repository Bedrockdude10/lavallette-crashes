#!/usr/bin/env python3
"""Build the street/intersection index the crash map places its pins from.

ONE Overpass query for the whole municipality (cached to data/osm_ways.json),
then every intersection is computed locally by looking for OSM nodes shared by
two differently-named ways. That replaces the per-intersection geocoding the
Hopewell version did: it is one request instead of dozens, it can't half-fail,
and it yields the real junction node rather than a geocoder's guess.

It also handles a DIVIDED highway. Route 35 through Lavallette is a one-way
pair -- Anna O Hankins Blvd and Grand Central Ave, about 140 m apart -- and NJDOT
crash records name only "NJ 35", never which barrel. So for each cross street the
script emits a `corridor`: the stretch of that cross street between the two
barrels. That segment is exactly what a person walks to get across Route 35, and
it is honest about the 140 m the crash record does not pin down.

  python3 scripts/build_streets.py --osm-area Lavallette --divided-ref "NJ 35"

Pass --refresh (or delete data/osm_ways.json) to re-query Overpass.
"""
import argparse, json, math, os, sys, urllib.parse, urllib.request, collections

UA = "lavallette-crash-map/1.0 (+github.com/bedrockdude10)"
ENDPOINTS = ["https://overpass-api.de/api/interpreter",
             "https://overpass.kumi.systems/api/interpreter",
             "https://overpass.private.coffee/api/interpreter"]
M_PER_DEG_LAT = 111320.0

# OSM ref prefix -> who maintains it. This is what the popup's agency line and
# any "who do I call" advice hangs off, so it is worth getting right: Route 35
# is a STATE highway, so most of Lavallette's crashes are NJDOT's to fix, not
# the borough's.
def jurisdiction(ref):
    r = (ref or "").upper()
    if r.startswith(("NJ ", "US ", "I ")):
        return "state"
    if r.startswith("CR "):
        return "county"
    return "borough"


def overpass(area):
    q = (f'[out:json][timeout:120];'
         f'area[name="{area}"][admin_level=8]->.a;'
         f'way(area.a)[highway][name];out geom;')
    last = None
    for attempt in range(6):
        ep = ENDPOINTS[attempt % len(ENDPOINTS)]
        try:
            data = urllib.request.urlopen(urllib.request.Request(
                ep, data=urllib.parse.urlencode({"data": q}).encode(),
                headers={"User-Agent": UA}), timeout=120).read()
            return json.loads(data)
        except Exception as e:
            last = e
            print(f"    [{ep.split('/')[2]}] {str(e)[:60]}", file=sys.stderr)
    sys.exit(f"Overpass failed after 6 attempts: {last}")


def midpoint(a, b):
    return [round((a[0] + b[0]) / 2, 6), round((a[1] + b[1]) / 2, 6)]


def metres(a, b):
    dlat = (b[0] - a[0]) * M_PER_DEG_LAT
    dlng = (b[1] - a[1]) * M_PER_DEG_LAT * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlng)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--osm-area", default="Lavallette")
    ap.add_argument("--divided-ref", default="NJ 35",
                    help="OSM ref of a highway carried on separate one-way barrels")
    ap.add_argument("--cache", default="data/osm_ways.json")
    ap.add_argument("--out", default="data/streets.json")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    if args.refresh or not os.path.exists(args.cache):
        print(f"Querying Overpass for {args.osm_area}...", file=sys.stderr)
        raw = overpass(args.osm_area)
        os.makedirs(os.path.dirname(args.cache), exist_ok=True)
        json.dump(raw, open(args.cache, "w"))
    else:
        raw = json.load(open(args.cache))
        print(f"Using cached {args.cache} (--refresh to re-query)", file=sys.stderr)

    ways = [e for e in raw.get("elements", [])
            if e.get("type") == "way" and e.get("geometry") and e.get("tags", {}).get("name")]
    if not ways:
        sys.exit("No named ways with geometry in the Overpass response.")

    # node id -> coords, and node id -> the street names meeting there
    coord, node2names = {}, collections.defaultdict(set)
    streets = {}
    for w in ways:
        t = w["tags"]
        name, ref = t["name"], t.get("ref", "")
        prev = streets.get(name)
        # A street's ref/class can differ per way; keep the one that carries a ref,
        # since that is what decides jurisdiction.
        if prev is None or (ref and not prev.get("ref")):
            streets[name] = {"ref": ref, "highway": t.get("highway", ""),
                             "jurisdiction": jurisdiction(ref)}
        for nd, g in zip(w.get("nodes", []), w["geometry"]):
            coord[nd] = (g["lat"], g["lon"])
            node2names[nd].add(name)

    # every junction of two differently-named streets
    inter = {}
    for nd, names in node2names.items():
        if len(names) < 2:
            continue
        ns = sorted(names)
        for i in range(len(ns)):
            for j in range(i + 1, len(ns)):
                key = f"{ns[i]}|{ns[j]}"
                inter.setdefault(key, []).append([round(coord[nd][0], 6),
                                                  round(coord[nd][1], 6)])

    # the divided highway's barrels, and one corridor per cross street
    barrel_names = sorted(n for n, s in streets.items()
                          if s["ref"] == args.divided_ref)
    corridors = {}
    if barrel_names:
        print(f"\n{args.divided_ref} is carried on {len(barrel_names)} named ways: "
              f"{', '.join(barrel_names)}", file=sys.stderr)
        cross_hits = collections.defaultdict(dict)
        for nd, names in node2names.items():
            bs = names & set(barrel_names)
            for other in names - set(barrel_names):
                for b in bs:
                    cross_hits[other][b] = [round(coord[nd][0], 6), round(coord[nd][1], 6)]
        for cross, hits in sorted(cross_hits.items()):
            pts = sorted(hits.items())
            if len(pts) < 2:
                # Meets the highway once only (an end, or a stub) -- no corridor to
                # draw, but still a real junction.
                continue
            # Widest-separated pair of barrels is the corridor's two ends.
            best = max(((a, b) for i, a in enumerate(pts) for b in pts[i + 1:]),
                       key=lambda ab: metres(ab[0][1], ab[1][1]))
            (na, pa), (nb, pb) = best
            corridors[cross] = {
                "ref": args.divided_ref,
                "jurisdiction": jurisdiction(args.divided_ref),
                "barrels": [{"name": na, "point": pa}, {"name": nb, "point": pb}],
                "line": [pa, pb],
                "midpoint": midpoint(pa, pb),
                "width_m": round(metres(pa, pb), 1),
            }

    out = {"area": args.osm_area,
           "divided_ref": args.divided_ref,
           "attribution": "Road geometry (c) OpenStreetMap contributors, ODbL",
           "streets": streets,
           "corridors": corridors,
           # keep only the first node of each pair; junctions with several shared
           # nodes (a wide mouth) are one intersection for our purposes
           "intersections": {k: v[0] for k, v in sorted(inter.items())}}
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    json.dump(out, open(args.out, "w"), indent=1)

    ws = [c["width_m"] for c in corridors.values()]
    print(f"\n{len(streets)} streets, {len(out['intersections'])} intersections, "
          f"{len(corridors)} {args.divided_ref} corridors", file=sys.stderr)
    if ws:
        print(f"corridor width across the pair: {min(ws):.0f}-{max(ws):.0f} m "
              f"(median {sorted(ws)[len(ws)//2]:.0f} m)", file=sys.stderr)
    print(f"-> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
