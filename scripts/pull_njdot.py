#!/usr/bin/env python3
"""Download NJDOT per-county annual crash files and keep one municipality's raw records.

NJDOT publishes at:
  https://www.state.nj.us/transportation/refdata/accident/<YEAR>/<County><YEAR>Accidents.zip
Data lags ~3 years; a 404 means that year isn't published yet, which is normal
and not an error.

Writes the raw <County><YEAR>Accidents.txt into --datadir for the later steps,
and prints a per-year count for the municipality so you can see the years landed.

  python3 scripts/pull_njdot.py --county Ocean --municipality "LAVALLETTE BORO" \
      --years 2018 2019 2020 2021 2022 --datadir scripts/data
"""
import argparse, io, os, sys, urllib.request, zipfile

BASE = "https://www.state.nj.us/transportation/refdata/accident"
UA = "lavallette-crash-map/1.0 (+github.com/bedrockdude10)"
I_MUNI = 2


def fetch_year(county, year, datadir):
    url = f"{BASE}/{year}/{county}{year}Accidents.zip"
    try:
        raw = urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": UA}), timeout=90).read()
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  {year}: not published yet (404)", file=sys.stderr)
        else:
            print(f"  {year}: HTTP {e.code} — skipping", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  {year}: FAILED ({type(e).__name__}: {e}) — skipping", file=sys.stderr)
        return None
    name = zf.namelist()[0]
    out = os.path.join(datadir, f"{county}{year}Accidents.txt")
    with open(out, "wb") as f:
        f.write(zf.read(name))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--county", default="Ocean")
    ap.add_argument("--municipality", default="LAVALLETTE BORO",
                    help="exactly as it appears in field 3 of the data")
    ap.add_argument("--years", nargs="+", type=int, required=True)
    ap.add_argument("--datadir", default="scripts/data")
    args = ap.parse_args()

    os.makedirs(args.datadir, exist_ok=True)
    total = 0
    for year in args.years:
        path = fetch_year(args.county, year, args.datadir)
        if not path:
            continue
        n = 0
        for line in open(path, encoding="latin-1"):
            p = line.split(",")
            if len(p) > I_MUNI and p[I_MUNI].strip() == args.municipality:
                n += 1
        total += n
        print(f"  {year}: {n} {args.municipality} crashes", file=sys.stderr)
    print(f"\n{total} records across the years that downloaded -> {args.datadir}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
