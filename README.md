# apScrap

Tracks rental listings in Greater Lisbon over time, so that choosing where to
live rests on data instead of on whatever was on the front page that week.

Every night it crawls Imovirtual, keeps every payload, builds a price history,
publishes a private static site and sends a Telegram message with what is new
or cheaper.

## What it gives you

- **Negócios.** Every rental ranked against its neighbours: same parish, type
  and typology, compared on both the headline price and €/m².
- **Negociar.** Adverts sitting for months or already cut in price, with their
  price path.
- **Novos.** What appeared in the last three days.
- **Mapa.** Every geocoded advert, coloured by how far below its neighbours it is.
- **Mercado.** Median €/m² per council over time and a parish price table.
- **Commute.** Public-transit minutes to work for every listing, from a grid
  computed with the AML timetables (GTFS) and OpenStreetMap.
- **Morning alert** on Telegram: new listings that match your budget and
  typologies, and the ones that just dropped.

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
                 export.py                    alert.py
          site/ + data.json                Telegram message
          -> Cloudflare Pages
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

The site is plain HTML, CSS and JS in `site/`. No build step: `export` copies
it next to a fresh `data.json`, and the browser does all the filtering.

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
| `apscrap commute-load FILE` | Load a travel-time grid from `scripts/commute_grid.py`. |
| `apscrap daily` | All of the above in order, for the targets in `cli.py`. |
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
| `APSCRAP_SITE_URL` | | Linked from the alert |

## The commute grid

Routing public transport over the whole metropolitan area takes several GB of
memory, and the timetables change a few times a year, so it is computed on a
laptop and loaded into the database:

```bash
/tmp/r5/bin/python scripts/commute_grid.py --lat .. --lon .. --label Trabalho \
    --osm lisboa.osm.pbf --gtfs tml-gtfs.zip --out ~/apscrap.commute.csv
apscrap commute-load ~/apscrap.commute.csv
```

The script's docstring lists the data sources and how to clip the OSM file.
The CSV reveals where you work, so it is git-ignored and never goes into this
public repository.

## Sources

| Portal | Status | Notes |
|---|---|---|
| Imovirtual | Implemented | Structured JSON in `__NEXT_DATA__`, no HTML parsing. ~4,500 rentals and ~16,500 sales in the district |
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
for one person's move, and there is no version of this that needs to go faster.

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
