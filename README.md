# apScrap

Tracks rental and sale listings in Greater Lisbon over time, and puts a small
app on top where two people decide together where to live and which car to
buy. Choosing rests on data instead of on whatever was on the front page that
week.

Every night it crawls Imovirtual, keeps every payload, builds a price history,
imports INE's official medians, and sends a Telegram message with what is new
or cheaper. The app (Casa & Carro) reads that warehouse live.

## What the app gives you

- **Imóveis.** Median €/m² per parish on a map of tiles, for rent or sale, with
  the 12-month change (INE until our own history is long enough), gross yield
  (rent against price), days on market, and the adverts below their neighbours'
  median with both people's votes.
- **Anúncios.** Every advert with filters (zone, typology, price, area, garage,
  lift, balcony, minutes to each workplace by transit or car), photos, a map with
  15/30/45-minute isochrones, and "save this search as an alert".
- **Detail.** Fair-price sentence ("12% abaixo da mediana de T2 em Loures,
  n=34"), price history, withdrawn-and-reposted chains, INE reference, commute
  for both, votes, and visit notes with photos.
- **Shortlist and Comparar.** Gosto / talvez / não from each person, grouped by
  agreement, and two to four items side by side.
- **Carros.** Models you follow: median price, a depreciation curve with the age
  where it flattens, the five-year cost of ownership (IUC, energy, insurance,
  maintenance, depreciation) and adverts rated against similar cars.
- **Assistente.** Questions in Portuguese, answered by an LLM that can only read
  (SQL on the views and a few domain tools) and must cite where every number
  comes from.
- **Alerts and the weekly diary** on Telegram: drops on saved items, new matches
  for saved searches, zones that fell, and a Monday summary.

REQUIREMENTS.md has the full list with what is done and why.

For each advert it records price, area, typology, floor, district / council /
parish, coordinates, build year, energy certificate, deposit, agency or
private owner, and the date the portal first published it.

## How it runs

```
imovirtual.com
     |  nightly CronJob in k3s (be-infra, apps/apscrap), 04:20 + jitter
     v
  fetch.py  ------------------>  raw_pages      (search pages 60 days, details forever)
                                     |
                                     |  parsed_at IS NULL == unprocessed
                                     v
                                 parse.py
                                     |
                       +-------------+-------------+
                       v                           v
                   listings                listing_snapshots
                (one per advert)          (one per advert per day)
                       |                           |
                       +-------------+-------------+
                                     v
                              SQL views (sql/002)
                                     |
                       +-------------+-------------+
                       v                           v
                 export.py                    alert.py, notify.py
      site_legacy/ + data.json           Telegram messages
   -> apscrap.pages.dev (old site)
                                     |
            relist.py, market.py (aggregates, mv_deals), ine.py
                                     |
                                     v
                       apscrap serve (web.py): site/ + /api
                       apscrap.beyouweb.com behind Cloudflare Access
```

The database is the `apscrap` database in the shared CloudNativePG cluster on
beyou-server. Deployment, secrets and backups live in
[be-infra](https://github.com/AndDev741/be-infra) under `apps/apscrap`; see
DEPLOY.md for the moving parts.

`raw_pages` is the queue. A page with `parsed_at IS NULL` is unprocessed work,
so a parser fix is: fix the code, clear `parsed_at`, run `apscrap parse`. Seen
dates and snapshot dates come from when each page was fetched, so a replay
rebuilds history as it was, not as of today.

## Local development

```bash
cp .env.example .env            # ports default to 5433 / 3002
docker compose up -d db
python3 -m venv .venv && .venv/bin/pip install -e .

.venv/bin/apscrap migrate
.venv/bin/apscrap crawl --transaction rent --location lisboa --max-pages 5
.venv/bin/apscrap parse
.venv/bin/apscrap export --out /tmp/site && python3 -m http.server -d /tmp/site 8000
```

The app is plain HTML, CSS and JS in `site/` served by `apscrap serve`, with no
build step. Locally, skip Cloudflare Access with a trusted email:

```bash
APSCRAP_DEV_USER=you@example.com .venv/bin/apscrap serve --port 8080
```

`site_legacy/` is the old static site that `export` publishes to Cloudflare
Pages with a fresh `data.json`, kept as a read-only fallback.

Metabase is still in `docker-compose.yml` with `scripts/setup_metabase.py` for
ad-hoc exploration on a laptop. Production does not run it.

## Commands

| Command | What it does |
|---|---|
| `apscrap migrate` | Create tables and views. Safe to re-run; the nightly job runs it first. |
| `apscrap crawl --transaction rent --type flat --location lisboa` | Fetch search pages into `raw_pages`. On Imovirtual `lisboa` is the whole district. |
| `apscrap details --limit 150` | Fetch detail pages for listings never enriched, rentals first. Adds coordinates. |
| `apscrap parse` | Drain `raw_pages` into `listings` and `listing_snapshots`. |
| `apscrap prune` | Delete parsed search pages older than `APSCRAP_RAW_KEEP_DAYS` (60). |
| `apscrap export --out DIR` | Build the static site into DIR. |
| `apscrap publish --dir DIR` | Deploy DIR to Cloudflare Pages with wrangler. |
| `apscrap alert [--dry-run]` | Send the Telegram message, or print it. |
| `apscrap commute-load FILE --slot a\|b` | Load a travel-time grid from `scripts/commute_grid.py` for one person. |
| `apscrap relist` | Match adverts that were withdrawn and posted again. |
| `apscrap aggregates` | Refresh `market_agg_daily` and the app's `mv_deals`. |
| `apscrap ine` | Import INE's parish medians (sales and new leases). |
| `apscrap fuel` | Fetch DGEG's average fuel prices for the car costs. |
| `apscrap reparse-details` | Re-read every stored detail page, after a parser change. |
| `apscrap notify [--dry-run]` | Drops on saved items, saved-search matches, zone alerts. |
| `apscrap diary [--force]` | Write this week's market diary (Mondays in `daily`). |
| `apscrap serve` | Run the app. |
| `apscrap daily` | The nightly sequence, for the targets in `cli.py`. |
| `apscrap stats` | What is in the warehouse right now. |

## Configuration

Environment variables, all optional except the database:

| Variable | Default | |
|---|---|---|
| `PGHOST` `PGPORT` `PGUSER` `PGPASSWORD` `PGDATABASE` | | Postgres |
| `APSCRAP_DELAY` | 3.0 | Seconds between requests |
| `APSCRAP_DETAIL_LIMIT` | 150 | Detail pages per night |
| `APSCRAP_RAW_KEEP_DAYS` | 60 | Search-page retention |
| `CLOUDFLARE_API_TOKEN` `CLOUDFLARE_ACCOUNT_ID` | | Turns on publishing. Token scope: Account, Cloudflare Pages, Edit |
| `APSCRAP_PAGES_PROJECT` | apscrap | Pages project name |
| `TELEGRAM_BOT_TOKEN` `TELEGRAM_CHAT_ID` | | Turns on the alert |
| `ALERT_MAX_PRICE` | 1300 | |
| `ALERT_TYPOLOGIES` | T1,T2,T3,T4,T5,T6 | |
| `ALERT_COUNCILS` | Lisboa,Odivelas,Loures | |
| `ALERT_MAX_COMMUTE` | unset | Minutes; listings without an estimate still pass |
| `APSCRAP_SITE_URL` | | Linked from the morning alert |
| `APSCRAP_APP_URL` | | The app, linked from the other alerts and the diary |
| `CF_ACCESS_TEAM_DOMAIN` `CF_ACCESS_AUD` | | The app checks Cloudflare Access's JWT against these |
| `APSCRAP_DEV_USER` | | Local development only: trust this email instead |
| `LLM_BASE_URL` `LLM_API_KEY` `LLM_MODEL` | | Any OpenAI-compatible endpoint for the assistant (OmniRoute in production) |
| `APSCRAP_RO_PGUSER` `APSCRAP_RO_PGPASSWORD` | | Read-only login for the assistant's SQL tool |

The `ALERT_*` variables are only the starting point now: the app's
Definições page stores the criteria in the database and wins over them.

## The commute grid

Routing public transport over the whole metropolitan area takes several GB of
memory, and the timetables change a few times a year, so it is computed on a
laptop and loaded into the database. One grid per person and mode (transit or
car; car times have no traffic model):

```bash
python scripts/commute_grid.py --lat .. --lon .. --label Trabalho --mode transit \
    --osm lisboa.osm.pbf --gtfs ... --out ~/apscrap-commute/a.transit.commute.csv
apscrap commute-load ~/apscrap-commute/a.transit.commute.csv --slot a
```

Saving a new workplace in the app marks that person's grids stale until they
are recomputed and loaded again.

The script's docstring lists the data sources and how to clip the OSM file.
The CSV reveals where you work, so it is git-ignored and never goes into this
public repository.

## Sources

| Portal | Status | Notes |
|---|---|---|
| Imovirtual | Implemented | Structured JSON in `__NEXT_DATA__`, no HTML parsing. ~7,000 rentals and ~20,000 sales in the district. Terms (19 Aug 2026, §2.7) allow personal use and forbid sharing aggregated data with third parties; this is a private app for two |
| INE | Implemented | Official parish medians: sales (0012234) and new leases (0014696), quarterly, imported weekly |
| Standvirtual | On demand | Cars. Its terms forbid repeated, systematic extraction, so a model is fetched only when someone follows it or presses Atualizar (at most weekly), and an advert only when someone pastes it |
| DGEG | Implemented | National average fuel prices for the car costs |
| Casa Sapo | Not yet | Responds to a plain request, needs an HTML parser |
| Remax, ERA | Not yet | Agency-only inventory, smaller and heavily overlapping |
| Idealista | Out of scope | Returns 403 even for `robots.txt`. Apply for their official API instead |
| Supercasa | Out of scope | Same bot protection |

Adding a source means writing one class with `search_url`, `search_payload` and
`parse_search_items`, then registering it in `apscrap/sources/__init__.py`.

## Being a good citizen

Three seconds between requests, one crawl a night, a random start inside a
20-minute window, and only paths that `robots.txt` allows. A full pass is about
600 search pages plus up to 500 detail pages, spread over an hour. The data is
for one family's move, and there is no version of this that needs to go faster.

## Caveats worth knowing

- `typology` comes from the T-rating in the advert title. Where the advertiser
  omitted it we derive it from the portal's room count and set
  `typology_inferred`.
- Area and price are whatever the advertiser typed. `is_plausible` flags the
  obvious typos and every aggregate excludes them. `is_junk` drops rooms, shops,
  plots, garages and sales posted as rentals, judged from the title.
- `days_on_market` uses the portal's first-published date, which resets if an
  agency deletes and relists.
- The deal score compares against a median of at least 8 neighbours. A listing
  in a quiet parish is compared with its council instead, and the site says so.

LIMITATIONS.md has the full list of what this data can and cannot tell you.
REQUIREMENTS.md describes where the project is heading: a decision room for two
people, with sales, cars and an agent that answers questions from the data.
