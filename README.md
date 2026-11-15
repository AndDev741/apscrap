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
imovirtual.com                           remax.pt, kwportugal.pt, era.pt, casa.sapo.pt
     |  apscrap-daily, 04:20 + jitter         |  apscrap-sources, 00:30 + jitter
     v                                        v
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
   relist.py, lifecycle.py (gone), dedup.py (same flat, two portals),
            market.py (aggregates, mv_deals), ine.py
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
| `apscrap sources [--only era,remax] [--force]` | Crawl RE/MAX, KW, ERA and Casa Sapo for the councils in Definições, then parse. `--force` lets Casa Sapo run outside the visit window its robots.txt asks for. |
| `apscrap relist` | Match adverts that were withdrawn and posted again. |
| `apscrap lifecycle` | Mark adverts that left their portal as gone, per portal (below). |
| `apscrap dedup` | Link the same flat advertised on several portals, so it shows and counts once. |
| `apscrap aggregates` | Refresh `market_agg_daily` and the app's `mv_deals`. |
| `apscrap ine` | Import INE's parish medians (sales and new leases). |
| `apscrap fuel` | Fetch DGEG's average fuel prices for the car costs. |
| `apscrap reparse-details` | Re-read every stored detail page, after a parser change. |
| `apscrap notify [--dry-run]` | Drops on saved items, saved-search matches, zone alerts. |
| `apscrap diary [--force]` | Write this week's market diary (Mondays in `daily`). |
| `apscrap serve` | Run the app. |
| `apscrap daily` | The nightly sequence for Imovirtual (targets in `cli.py`), then relist, lifecycle, dedup, aggregates and the app's read models for every portal. |
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

| Portal | Status | How | Notes |
|---|---|---|---|
| Imovirtual | Nightly | Search pages, structured JSON in `__NEXT_DATA__`; detail pages for coordinates | ~7,000 rentals and ~20,000 sales in the district. Terms (19 Aug 2026, §2.7) allow personal use and forbid sharing aggregated data with third parties; this is a private app for two |
| RE/MAX | Nightly, rentals | Listing sitemaps (allowed), then the detail page of new, changed (sitemap `lastmod`) or week-old adverts; the advert is base64 JSON in `__NEXT_DATA__` | ~760 rentals in Lisboa, Odivelas and Loures. Exact coordinates and publish date. `/api` is disallowed, so the search API is not used. Keeps some let flats online (`isSold`) |
| KW | Nightly, rentals | Property sitemaps, then detail pages (Next.js flight data) | ~1,000 rentals in the sitemaps for the three councils, about 80% already let: KW keeps them online with `isRented`, which is the signal we use. Let adverts are not re-read |
| ERA | Nightly, rentals | The search page's JSON API (allowed), plus a detail endpoint once per advert | ~130 in the three councils. Keeps let flats as "Já ERA" and reserved ones as "Reservado" |
| Casa Sapo | Nightly, rentals | HTML search pages (allowed); every card carries a JSON-LD Offer with coordinates and the seller type | ~1,700 homes in the three councils, a third from private owners. Rate-limited hard: 45 s between pages, so about an hour. Only between 23:00 and 11:00 UTC, as its robots.txt asks |
| INE | Weekly | JSON indicator API | Official parish medians: sales (0012234) and new leases (0014696) |
| Standvirtual | On demand | Search pages for the models you follow | Cars. Its terms forbid repeated, systematic extraction, so a model is fetched only when someone follows it or presses Atualizar (at most weekly), and an advert only when someone pastes it |
| DGEG | Weekly | | National average fuel prices for the car costs |
| Idealista | No | | Bot protection (DataDome). The official API needs an application |
| OLX, Supercasa, Century 21, CustoJusto | No | | OLX and Supercasa block plain requests (403, Cloudflare challenge); Century 21's robots.txt disallows its API and filtered search; CustoJusto's forbids automated access. About 500 OLX adverts reach us through Imovirtual anyway |

The other portals crawl rentals only, for the councils in Definições
(`property_criteria.councils`), and each owns its walk of the site:
`crawl(ctx)` stores pages in `raw_pages`, `parse_page(kind, payload)` turns
them into listings and snapshots. A new one is a class in `apscrap/sources/`
registered in `__init__.py`. Council and parish names are brought to CAOP's
(`apscrap/geo.py`): from the coordinates when the advert has them, using
`site/geo/freguesias.geojson`, else from the portal's own wording.

### Price changes, removals, the same flat twice

- **Price changes.** Every live advert gets one snapshot a day (sitemap
  portals carry yesterday's price over until the detail page says otherwise).
  A change counts as a move from 1% for rent and 0.5% for sale; below that it
  is a €5 nudge to climb the results or a platform's daily repricing.
  Placeholder prices (Uniplaces posts €1 between tenants) never count.
- **Removals.** `lifecycle.py` marks an advert gone once its portal has been
  crawled cleanly for 60 hours without it, counting from that portal's newest
  observation, so a failed crawl never empties the site. `gone_reason` says
  why: `missing`, `relisted` (a newer advert reposts it, see `relist.py`),
  `removed` (its page answers 404/410) or `rented` (ERA, KW and RE/MAX say so).
  An advert that comes back is active again. The app lists what left in the
  last 14 days and how long adverts last by how cheap they were.
- **Mid-stay platforms.** Uniplaces, Spacest and UKIO let furnished flats by
  the month at about 35 €/m² against 22. They are a third of Imovirtual's
  rentals, so they are kept out of every median and deal score, and the app
  shows them only when asked.
- **The same flat on several portals.** `dedup.py` groups live adverts that
  agree on type, typology, council, price (3%), area (5%) and place (400 m, or
  the same parish), with tighter limits when the advertisers differ. The app
  shows one card with "Também em" links, and medians count it once.
- **A portal's first night.** Adverts already online the first time we crawl
  a portal are not "new" (`is_backlog`), and their days on market stay unknown
  unless the portal gives a publish date.

## Being a good citizen

Three seconds between requests (45 s on Casa Sapo), one crawl a night per
portal, a random start inside a 20-minute window, and only paths that
`robots.txt` allows, inside the visit hours it asks for. Imovirtual is about
600 search pages plus 150 detail pages; the other portals add a few sitemaps,
the detail pages of new and changed adverts, and Casa Sapo's 75 pages. The data
is for one family's move, and there is no version of this that needs to go
faster.

## Caveats worth knowing

- `typology` comes from the T-rating in the advert title. Where the advertiser
  omitted it we derive it from the portal's room count and set
  `typology_inferred`.
- Area and price are whatever the advertiser typed. `is_plausible` flags the
  obvious typos and every aggregate excludes them. `is_junk` drops rooms, shops,
  plots, garages and sales posted as rentals, judged from the title.
- `days_on_market` uses the portal's first-published date, which resets if an
  agency deletes and relists (`relist.py` follows the chain). ERA, KW and Casa
  Sapo give no publish date, so their days count from when we first saw them.
- The deal score compares against a median of at least 8 neighbours. A listing
  in a quiet parish is compared with its council instead, and the site says so.

LIMITATIONS.md has the full list of what this data can and cannot tell you.
REQUIREMENTS.md describes where the project is heading: a decision room for two
people, with sales, cars and an agent that answers questions from the data.
