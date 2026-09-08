# Lavallette Crash Map

Where crashes happen in Lavallette Borough, NJ, and which Route 35 crossings
carry the most harm. One HTML file, two data files, no build step, no server.

Ported from the Hopewell Borough crash map. The crash pipeline carried over
almost unchanged; the crosswalk-survey layer did not come with it, because that
was field work rather than code.

## What it shows

**203 of 209** recorded crashes, 2018–2022, from NJDOT's public per-county crash
files. Pins are coloured by who was involved and ringed by severity, exactly as
in the Hopewell build.

The new layer is **Route 35 crossings**. Route 35 runs the length of the borough
as *two* one-way roadways about 140 m apart — Anna O Hankins Boulevard and Grand
Central Avenue — so each side street crosses the highway twice. Each bar on the
map is the stretch of one side street between the two roadways: what a person
actually crosses to get from the bay side to the beach. Bars are shaded by how
much crash harm is recorded there, and the panel on the right ranks the worst
five.

As of the 2018–2022 data:

| # | Crossing | Crashes | On foot or bike | Harm score |
|---|---|---|---|---|
| 1 | Reese Avenue | 17 | 2 | 27.0 |
| 2 | Guyer Avenue | 12 | 2 | 24.5 |
| 3 | New Brunswick Avenue | 10 | 2 | 20.5 |
| 4 | Brown Avenue | 9 | 2 | 20.0 |
| 5 | Kerr Avenue | 7 | 2 | 19.0 |

Three things fall out of the data that are worth saying out loud to anybody
this gets shown to:

- **190 of the 209 crashes are on Route 35, which is a _state_ highway.** The
  agency that can change the geometry, the signals, or the crossings is NJDOT,
  not the borough and not the county. Every corridor popup says so.
- **The crashes are seasonal.** July and August alone are 95 of 209 — 45% of five
  years' crashes in two months a year — and 13 of the 16 crashes involving
  someone on foot or on a bike happened June–September. A per-resident rate for a
  town of ~2,000 year-round residents is meaningless here.
- **Turning off "Vehicle only" is the most useful thing on the page.** It drops
  to the 16 crashes where somebody outside a vehicle was hit, and the ranking
  re-sorts: Guyer Avenue goes to #1, on the strength of the borough's one
  pedestrian fatality (19 Oct 2019).

## The harm score

Deliberately simple and additive, so it can be argued with rather than taken on
faith. Computed in the browser from whatever crashes are currently visible, so
the numbers always match the pins and the type filters.

| | Fatal | Injury | Property damage |
|---|---|---|---|
| Pedestrian or cyclist involved | 12 | 6 | 2 |
| Vehicle only | 4 | 2 | 0.5 |

Someone hurt *outside* a vehicle counts for roughly double someone inside one:
on a beach street the exposed user is the one the street design is failing.

This is why President Avenue is not in the top five despite having the most
crashes of any crossing (18). They are almost all property-damage — parking and
backing collisions — and the ranking is about harm, not volume. Both numbers are
in every popup so you can see which is which.

### Limits, all of them printed on the page too

- **Proximity is not cause.** A crash logged at "NJ 35 and Reese Ave" was not
  necessarily *at* the crossing.
- **Each bar covers about 140 m.** NJDOT records name the street pair but never
  which of the two roadways, so the crash genuinely cannot be placed on one of
  them. The corridor is drawn at exactly the precision the data has.
- **Nothing recent is in here.** NJDOT runs about three years behind; 2022 is the
  latest published year as of September 2026. There is no current-year data.
- **Only 5 closed years.** 209 crashes is enough to rank corridors and much more
  than the Hopewell build had, but single-crossing differences of one or two
  crashes are noise.

## Rebuilding the data

Nothing here needs credentials or third-party packages — standard library only.

```bash
python3 scripts/pull_njdot.py --county Ocean --municipality "LAVALLETTE BORO" \
    --years 2018 2019 2020 2021 2022 --datadir scripts/data
python3 scripts/build_streets.py --osm-area Lavallette --divided-ref "NJ 35"
python3 scripts/build_rows.py
```

| Script | What it does |
|---|---|
| `pull_njdot.py` | Downloads NJDOT's per-county annual files into `scripts/data/` (gitignored). A 404 is a year not yet published, not an error. |
| `build_streets.py` | **One** cached Overpass query → `data/streets.json`: every street, every junction, and the 28 Route 35 corridors. `--refresh` to re-query. |
| `build_rows.py` | Resolves each crash's street pair against real OSM names, places the pin, writes `data/crashes.csv`. |

When NJDOT publishes 2023, add it to `--years`, re-run `pull_njdot.py` and
`build_rows.py`, and update `YEARS_LABEL` in `index.html`. `build_streets.py`
only needs re-running if the road network changed.

### How street names are matched

NJDOT's location text is hand-entered and messy. Rather than the hardcoded
per-street `if` chain the Hopewell version used, `build_rows.py` normalises names
(full-word suffixes, punctuation and spaces dropped) and matches them against the
street names OSM actually has, with a near-match fallback. That resolves real
typos in the source data — `BRYMAWR AVENUE` → Bryn Mawr Avenue, `PERHSING BLVD`
→ Pershing Boulevard, `GR CENTRAL AVE` → Grand Central Avenue — and route
designations mixed in with street names, like `CR 8 / WASHINGTON AVE`.

**Every substitution it makes is printed on each run.** Read that list; it is the
only thing standing between a typo and a pin in the wrong place. It also prints
any road text it could not resolve at all.

A field can name more than one street (`LAVALLETTE AVE / NEW YORK AVE`), so every
reading is kept and the junction lookup picks the pair that actually meets.

### The 6 crashes with no pin

They name one street and no cross street — mid-block, with nothing to place them
against:

```
L18-0400  PERHSING BLVD          L21-1980  VANCE AVE
L18-0840  51 DICKMAN DR          22-32137  PLOVER WAY; OCEAN BE
C060-2019-00361A  NJ 35          L22-1698  NJ 35
```

They stay in `data/crashes.csv` with empty `lat`/`lng` — dropping them would
quietly understate the count — and the map skips them. Two of them are "NJ 35"
with no cross street at all, which locates a crash somewhere along 3 km of
highway. If you want them placed, put coordinates in
`scripts/manual_overrides.json` as `{"CASE_ID": [lat, lng]}`; that file is read
if present and overrides everything else.

## If somebody wants to maintain this by hand

The Hopewell build read its crashes live from a published Google Sheet, because
a volunteer was adding local police reports as they came in. This one is five
closed years of state data, so it is a file in the repo instead.

The hook is still there. Set `SHEET_URL` near the top of the `<script>` block in
`index.html` to a "Publish to web" CSV link and the site reads that instead of
`data/crashes.csv`. Give the Sheet the same header row as `data/crashes.csv`
(including the `corridor` and `jurisdiction` columns — `corridor` must match a
key in `data/streets.json` for a crash to count toward a crossing's score).

## Running and deploying

The page uses `fetch` for both data files, so it needs to be served over HTTP —
opening `index.html` off the filesystem shows an empty map.

```bash
python3 -m http.server 8000
```

For GitHub Pages: push to `main`, then Settings → Pages → Deploy from a branch →
`main` / `/ (root)`. `index.html` is at the repo root. The whole site is
`index.html`, `data/crashes.csv`, and `data/streets.json` — about 300 KB with no
photos, so none of the Hopewell repo's git push-buffer trouble applies.

## What was dropped in the port

- **The crosswalk inventory layer**, and with it `build_crosswalks.py`,
  `osm_crossings.py`, the survey photos, and the paving/priority scoring. That
  layer was a field survey — someone walked Hopewell and photographed 84
  crossings. Nothing in it could be ported, only redone.
- **The paving data and "next due" estimates.** Route 35 is NJDOT's, so the
  Mercer County and borough cycle assumptions do not transfer at all.
- **The Google Sheet backend and `append_crashes.py`**, along with the
  `gspread`/`google-auth` dependency and the service-account key.

If the crosswalk survey ever happens here, the honest place to put it is a
second layer scored against these corridors — the ranking already tells you
which 5 of the 28 crossings to walk first.

Road geometry © OpenStreetMap contributors, ODbL. Crash data: NJDOT.
