#!/usr/bin/env python3
"""Turn raw NJDOT crash records into map-ready rows for one municipality.

Reads *Accidents.txt from --datadir plus data/streets.json (scripts/build_streets.py),
resolves each record's location + cross street against the real OSM street names,
places the pin, and writes the site's CSV schema.

Three things this does that are worth knowing:

* Crash TYPE comes from the NJTR-1 crash-type code (13 = pedestrian, 14 =
  pedalcyclist), NOT from the "pedestrians killed/injured" count fields. Those
  fields are 0 for 12 of Lavallette's 14 bicycle crashes, so reading them instead
  loses almost every bicycle crash in the dataset.

* Street names are matched by NORMALISING to OSM's spelling, then falling back to
  a near-match. NJDOT's text is hand-entered and contains real typos --
  "BRYMAWR AVENUE" for Bryn Mawr, "PERHSING BLVD" for Pershing -- and route
  designations mixed in with street names ("CR 8 / WASHINGTON AVE"). Every
  substitution is printed so it can be checked.

* Records on the divided highway are placed on the CORRIDOR, mid-way between the
  two one-way barrels, and tagged with the corridor's cross street. NJDOT records
  say only "NJ 35"; they never say which barrel, and the barrels are ~140 m apart.
  Pretending to know is the one thing the placement must not do.

  python3 scripts/build_rows.py --municipality-name "LAVALLETTE BORO" \
      --municipality-label "Lavallette Borough" --out data/crashes.csv
"""
import argparse, csv, difflib, glob, json, math, os, re, sys, collections

# 0-based comma-field indices, per CrashTable.pdf and validated against records.
# Parse by comma order, NOT character position: the text fields are not padded
# to the spec's widths, so character offsets drift.
I_KEY, I_MUNI, I_DATE, I_TIME = 0, 2, 3, 5
I_KILLED, I_INJURED, I_PEDK, I_PEDI = 9, 10, 11, 12
I_SEVERITY, I_CRASHTYPE, I_NVEH = 13, 17, 18
I_LOCATION, I_DIST, I_UNIT, I_DIR, I_CROSS = 19, 35, 36, 37, 38
# The records are 50 fields wide, not 47. These four are the difference between
# "somewhere along a 140 m corridor" and "on the northbound roadway":
#   [20] travel direction on the route (N/S/E/W)  -- populated for 178 of 209
#   [21] route number                             -- "35", "629"
#   [24] mile post
#   [45]/[46] latitude / longitude                -- populated for 111 of 209
I_ROUTE_DIR, I_ROUTE_NO, I_MILEPOST = 20, 21, 24
I_LAT, I_LNG = 45, 46
MIN_FIELDS = 50

# A record's own coordinate is trusted only if it lands this close to the roadway
# the record names. Beyond it the coordinate is wrong, not the roadway: 6 of
# Lavallette's Route 35 records sit 88-795 m from any part of Route 35.
COORD_TRUST_M = 40.0

CT = {
    "01": ("vehicle", "rear-end collision"), "02": ("vehicle", "same-direction sideswipe"),
    "03": ("vehicle", "right-angle collision"), "04": ("vehicle", "opposite-direction collision"),
    "05": ("vehicle", "opposite-direction sideswipe"), "06": ("vehicle", "collision with a parked vehicle"),
    "07": ("vehicle", "left-turn or U-turn collision"), "08": ("vehicle", "backing collision"),
    "09": ("vehicle", "encroachment collision"), "10": ("other", "overturn"),
    "11": ("vehicle", "collision with a fixed object"), "12": ("other", "collision with an animal"),
    "13": ("pedestrian", "pedestrian crash"), "14": ("bicycle", "crash involving a bicyclist"),
    "15": ("other", "collision with a non-fixed object"), "16": ("other", "railcar collision"),
}
SEV = {"F": ("fatal", "Fatal"), "I": ("injury", "Injury"), "P": ("property_damage", "Property-damage")}
DIRV = {"N": (1, 0), "S": (-1, 0), "E": (0, 1), "W": (0, -1)}

SUFFIX = {"AVE": "AVENUE", "AV": "AVENUE", "ST": "STREET", "RD": "ROAD", "PL": "PLACE",
          "DR": "DRIVE", "LN": "LANE", "CT": "COURT", "BLVD": "BOULEVARD",
          "BLV": "BOULEVARD", "PKWY": "PARKWAY", "TER": "TERRACE", "WY": "WAY",
          "CIR": "CIRCLE", "HWY": "HIGHWAY", "BE": "BEACH"}
M_PER_DEG_LAT = 111320.0


def haversine_m(a, b):
    dlat = (b[0] - a[0]) * M_PER_DEG_LAT
    dlng = (b[1] - a[1]) * M_PER_DEG_LAT * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlng)


def norm(s):
    """Comparison key: full-word suffixes, no punctuation, no spaces.

    Dropping spaces is what makes OSM's "Bryn Mawr Avenue" and NJDOT's
    "BRYNMAWR AVE" the same street without a hand-written alias for it.
    """
    s = re.sub(r"[^A-Z0-9 ]", " ", s.upper())
    s = re.sub(r"\s+", " ", s).strip()
    words = [SUFFIX.get(w, w) for w in s.split()]
    return "".join(words)


def route_ref(tok):
    """'OCEAN COUNTY 629' / 'CR 629' / 'ROUTE 35 NORTH' / 'NJ 35Z' -> a canonical ref."""
    t = re.sub(r"[^A-Z0-9 ]", " ", tok.upper())
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\b(NORTH|SOUTH|EAST|WEST|NB|SB|EB|WB)\b", "", t).strip()
    m = re.match(r"^(?:[A-Z]+ COUNTY|CR|COUNTY(?: ROUTE)?)\s+(\d+)[A-Z]?$", t)
    if m:
        return f"CR {m.group(1)}"
    m = re.match(r"^(?:NJ|ROUTE|RT|SR|STATE(?: HIGHWAY)?)\s+(\d+)[A-Z]?$", t)
    if m:
        return f"NJ {m.group(1)}"
    m = re.match(r"^(?:US|I)\s+(\d+)[A-Z]?$", t)
    if m:
        return t.split()[0] + " " + m.group(1)
    return None


class Streets:
    def __init__(self, path):
        d = json.load(open(path))
        self.divided_ref = d["divided_ref"]
        self.streets = d["streets"]
        self.corridors = d["corridors"]
        self.barrel_directions = d.get("barrel_directions", {})
        self.intersections = d["intersections"]
        self.by_norm = {norm(n): n for n in self.streets}
        self.by_ref = collections.defaultdict(list)
        for n, s in self.streets.items():
            if s.get("ref"):
                self.by_ref[s["ref"]].append(n)
        self.subs = {}      # raw token -> resolved, for the report
        self.unresolved = collections.Counter()

    def resolve(self, tok):
        """A raw NJDOT road token -> (list of street names, list of refs).

        A field can hold several identities -- "CR 8 / WASHINGTON AVE",
        "LAVALLETTE AVE / NEW YORK AVE", "PLOVER WAY; OCEAN BE" -- and which one
        pins the crash depends on what the OTHER field says, so every reading is
        kept and the junction lookup picks the pair that actually meets.
        """
        tok = re.sub(r"\*+", "", tok).strip()
        tok = re.sub(r"^\d+\s+", "", tok)          # drop a house number: "51 DICKMAN DR"
        if not tok:
            return [], []
        names, refs = [], []
        for part in [x.strip() for x in re.split(r"[/;]", tok) if x.strip()]:
            r = route_ref(part)
            if r:
                if r not in refs:
                    refs.append(r)
                continue
            k = norm(part)
            if k in self.by_norm:
                nm = self.by_norm[k]
            else:                                   # near-match for hand-entry typos
                close = difflib.get_close_matches(k, list(self.by_norm), n=1, cutoff=0.86)
                if not close:
                    self.unresolved[part] += 1
                    continue
                nm = self.by_norm[close[0]]
                self.subs.setdefault(part, f"{nm}  (near-match)")
            self.subs.setdefault(part, nm)
            if nm not in names:
                names.append(nm)
        return names, refs

    def expand(self, names, refs):
        """All street names a field could mean, named ones first."""
        out = list(names)
        for r in refs:
            for n in self.by_ref.get(r, []):
                if n not in out:
                    out.append(n)
        return out

    def is_divided(self, name, refs):
        return (self.divided_ref in refs
                or self.streets.get(name, {}).get("ref") == self.divided_ref)

    def barrel_at(self, cross, direction):
        """The point where the barrel travelling `direction` meets `cross`.

        This is what turns "NJ 35 at Reese Ave" into a point on the northbound
        roadway rather than a point 70 m from each of two roadways.
        """
        c = self.corridors.get(cross)
        if not c or not direction:
            return None, None
        for b in c["barrels"]:
            if b.get("direction") == direction:
                return b["name"], b["point"]
        return None, None

    def junction(self, a_names, a_refs, b_names, b_refs):
        """-> (lat, lng, label_road, corridor_cross | None)."""
        # On the divided highway, place on the corridor keyed by the OTHER street:
        # the record says "NJ 35" and never which of the two barrels.
        for (mine, myrefs), (theirs, theirrefs) in (((a_names, a_refs), (b_names, b_refs)),
                                                    ((b_names, b_refs), (a_names, a_refs))):
            div = self.is_divided(None, myrefs) or any(self.is_divided(n, []) for n in mine)
            if not div:
                continue
            for other in self.expand(theirs, theirrefs):
                if other in self.corridors:
                    c = self.corridors[other]
                    return c["midpoint"][0], c["midpoint"][1], self.divided_ref, other
        for x in self.expand(a_names, a_refs):
            for y in self.expand(b_names, b_refs):
                if x == y:
                    continue
                pt = self.intersections.get(f"{min(x, y)}|{max(x, y)}")
                if pt:
                    return pt[0], pt[1], x, None
        return None, None, None, None


def offset(lat, lng, dist_ft, direction):
    v = DIRV.get(direction)
    if not v or not dist_ft:
        return lat, lng
    m = dist_ft * 0.3048
    lat2 = lat + v[0] * m / M_PER_DEG_LAT
    lng2 = lng + v[1] * m / (M_PER_DEG_LAT * math.cos(math.radians(lat)))
    return round(lat2, 6), round(lng2, 6)


def g(p, i):
    return p[i].strip() if i < len(p) else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--datadir", default="scripts/data")
    ap.add_argument("--streets", default="data/streets.json")
    ap.add_argument("--municipality-name", default="LAVALLETTE BORO")
    ap.add_argument("--municipality-label", default="Lavallette Borough")
    ap.add_argument("--overrides", default="scripts/manual_overrides.json")
    ap.add_argument("--out", default="data/crashes.csv")
    args = ap.parse_args()

    st = Streets(args.streets)
    overrides = json.load(open(args.overrides)) if os.path.exists(args.overrides) else {}
    files = sorted(glob.glob(os.path.join(args.datadir, "*Accidents.txt")))
    if not files:
        sys.exit(f"No *Accidents.txt in {args.datadir} — run scripts/pull_njdot.py first")

    rows, no_pin = [], []
    stats = collections.Counter()
    for path in files:
        for line in open(path, encoding="latin-1"):
            p = [c.strip() for c in line.split(",")]
            if len(p) < MIN_FIELDS or p[I_MUNI] != args.municipality_name:
                continue
            case = g(p, I_KEY)[8:].strip()
            mm, dd, yy = g(p, I_DATE).split("/")
            t = g(p, I_TIME).zfill(4)
            ctype, phrase = CT.get(g(p, I_CRASHTYPE), ("vehicle", "crash"))
            sev, sev_word = SEV.get(g(p, I_SEVERITY), ("other", "Crash"))
            nveh = g(p, I_NVEH)
            dist, unit, direction = g(p, I_DIST), g(p, I_UNIT), g(p, I_DIR)
            at_int = unit == "AT" or not dist

            a_names, a_refs = st.resolve(g(p, I_LOCATION))
            b_names, b_refs = st.resolve(g(p, I_CROSS))
            lat, lng, road, corridor = st.junction(a_names, a_refs, b_names, b_refs)

            # ---- which roadway of the divided highway? ----
            # Preference order, most to least direct:
            #   1. the record names a barrel outright ("NJ 35 / GRAND CENTRAL AVE")
            #   2. the route-direction field, matched to the barrel that travels
            #      that way (N -> Grand Central, S -> Anna O Hankins here)
            barrel = barrel_dir = ""
            if corridor:
                named = [n for n in (a_names + b_names) if n in st.barrel_directions]
                if named:
                    barrel = named[0]
                    barrel_dir = st.barrel_directions[barrel]
                    stats["barrel_named"] += 1
                elif g(p, I_ROUTE_NO) == st.divided_ref.split()[-1]:
                    rd = g(p, I_ROUTE_DIR)
                    bname, bpoint = st.barrel_at(corridor, rd)
                    if bname:
                        barrel, barrel_dir = bname, rd
                        stats["barrel_from_direction"] += 1
                if barrel:
                    bn, bp = st.barrel_at(corridor, barrel_dir)
                    if bp:
                        lat, lng = bp[0], bp[1]
                else:
                    stats["barrel_unknown"] += 1

            # ---- the record's own coordinate, where it is trustworthy ----
            # More precise than a junction node when it is right, and demonstrably
            # wrong often enough that it has to be checked against the roadway the
            # record names before being believed.
            rlat, rlng = g(p, I_LAT), g(p, I_LNG)
            if rlat and rlng and lat is not None:
                try:
                    cand = (float(rlat), -abs(float(rlng)))
                except ValueError:
                    cand = None
                if cand and 38.5 <= cand[0] <= 41.5 and -75.6 <= cand[1] <= -73.8:
                    off = haversine_m(cand, (lat, lng))
                    if off <= COORD_TRUST_M:
                        lat, lng = round(cand[0], 6), round(cand[1], 6)
                        stats["coord_used"] += 1
                    else:
                        stats["coord_rejected"] += 1

            road_label = (road or (a_names[0] if a_names else "")
                          or (a_refs[0] if a_refs else "") or g(p, I_LOCATION).title())
            if road_label == st.divided_ref:
                road_label = "Route 35"
            cross_label = corridor or (b_names[0] if b_names else "") or (b_refs[0] if b_refs else "")
            if corridor:
                dir_word = {"N": "North", "S": "South", "E": "East", "W": "West"}.get(barrel_dir, "")
                loc_label = (f"Route 35 {dir_word} & {corridor}" if dir_word
                             else f"Route 35 & {corridor}")
            elif cross_label:
                loc_label = (f"{road_label} & {cross_label}" if at_int
                             else f"{road_label} near {cross_label}")
            else:
                loc_label = road_label

            if case in overrides:
                lat, lng = overrides[case]
            elif lat is not None and not at_int and not corridor:
                # Only offset off a plain junction. A corridor pin is already an
                # explicit "somewhere along here"; nudging it would imply precision.
                lat, lng = offset(lat, lng, float(dist or 0), direction)

            if ctype in ("pedestrian", "bicycle"):
                who = "a pedestrian" if ctype == "pedestrian" else "a bicyclist"
                desc = f"{sev_word} crash involving {who} and a vehicle on {road_label}"
            else:
                veh = f"{nveh} vehicles" if nveh and nveh != "1" else "a vehicle"
                desc = f"{sev_word} {phrase} involving {veh} on {road_label}"
            if cross_label:
                desc += (f" at {cross_label}" if at_int
                         else f" about {dist} ft {direction} of {cross_label}")
            desc += ". (Source: NJDOT crash record.)"

            row = dict(id=case, date=f"{yy}-{mm}-{dd}",
                       time=f"{t[:2]}:{t[2:]}" if t.strip("0") else "",
                       municipality=args.municipality_label, location=loc_label,
                       lat=lat if lat is not None else "",
                       lng=lng if lng is not None else "",
                       crash_type=ctype, severity=sev,
                       corridor=corridor or "",
                       barrel=barrel,
                       direction=barrel_dir,
                       jurisdiction=(st.corridors[corridor]["jurisdiction"] if corridor
                                     else st.streets.get(road_label, {}).get("jurisdiction", "")),
                       description=desc, source_url="")
            rows.append(row)
            if lat is None:
                no_pin.append((case, g(p, I_LOCATION), g(p, I_CROSS)))

    rows.sort(key=lambda r: (r["date"], r["time"]))
    hdr = ["id", "date", "time", "municipality", "location", "lat", "lng",
           "crash_type", "severity", "corridor", "barrel", "direction",
           "jurisdiction", "description", "source_url"]
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=hdr)
        w.writeheader(); w.writerows(rows)

    print("\n--- street-name substitutions (check these) ---", file=sys.stderr)
    for raw, to in sorted(st.subs.items()):
        if norm(raw) != norm(to):
            print(f"  {raw:34} -> {to}", file=sys.stderr)
    if st.unresolved:
        print("\n--- UNRESOLVED road text ---", file=sys.stderr)
        for raw, n in st.unresolved.most_common():
            print(f"  x{n:<3} {raw}", file=sys.stderr)
    if no_pin:
        print(f"\n--- {len(no_pin)} records with no pin ---", file=sys.stderr)
        for case, a, b in no_pin:
            print(f"  {case:14} {a[:30]:32} | {b[:28]}", file=sys.stderr)
    print("\n--- roadway assignment on the divided highway ---", file=sys.stderr)
    for k in ("barrel_named", "barrel_from_direction", "barrel_unknown",
              "coord_used", "coord_rejected"):
        print(f"  {k:24}{stats[k]}", file=sys.stderr)

    placed = sum(1 for r in rows if r["lat"] != "")
    onc = sum(1 for r in rows if r["corridor"])
    onb = sum(1 for r in rows if r["barrel"])
    print(f"\n{len(rows)} rows, {placed} placed ({len(rows)-placed} unplaced), "
          f"{onc} on a Route 35 corridor, {onb} pinned to a specific roadway "
          f"-> {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
