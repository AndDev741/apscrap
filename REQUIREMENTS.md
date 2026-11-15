# Requirements

apScrap started as a rental tracker for one person. These requirements turn it
into a decision room for two people choosing a home, and possibly a car,
together. The data exists to answer concrete questions: is this expensive for
the area, does buying beat renting here, which of the two do we prefer.

Each requirement has an ID, a status measured against the code on 2026-11-15
(**exists**, **partial** or **new**) and what has to be true for it to count as
done. Section 8 records the decisions behind them.

## Where things stand (measured 2026-11-15)

| | |
|---|---|
| Nightly run | Green for the last three nights, 60 to 73 minutes each |
| History | 19 snapshot days between 2026-09-15 and 2026-11-15. Nothing from 2026-09-16 to 2026-10-28, nightly since 2026-10-30 |
| Rentals | 6,977 listings, 6,314 with coordinates |
| Sales | 20,236 listings (flats only), 1,493 with coordinates |
| Server | About 1 GB of RAM available on beyou-server, swap already in use |
| Standvirtual | robots.txt allows `/` for every agent; search pages carry `__NEXT_DATA__` like Imovirtual |
| Idealista | Still answers 403 for robots.txt |

## Principles

These hold for every requirement below.

- **The nightly crawl comes first.** New steps run after it and may fail
  without costing a day of history, as `_daily` in `cli.py` already arranges.
  Anything that needs more than the server's spare memory waits.
- **Every comparison shows its sample.** A median carries its n and its peer
  group. Below 8 listings the comparison falls back to the wider group, the rule
  `v_deals` uses today, or says it has no answer.
- **Asking prices are asking prices.** Wherever INE publishes an official
  figure, the page shows it next to the listing figure.
- **The repository is public.** Workplaces, emails, visit notes, photos and
  conversations with the agent stay out of git and out of the public site files.

## 1. Access

**AC-1 Two named users** (new). Both people sign in, and every write (vote,
note, photo, alert, question to the agent) records who made it.
Done when the Cloudflare Access policy lists both emails, the service takes the
identity from the validated `Cf-Access-Jwt-Assertion` header, and nobody else
gets past Access.

**AC-2 Private by default** (exists for the static site). Nothing is reachable
without Access. Photos and notes are served only through the authenticated
service.

Access already guards apscrap.beyouweb.com and Headlamp, so simple login costs
one more email in the policy. Tailscale-only access is the fallback if Access
ever gets in the way.

## 2. Property

### IM-1 Heat map by parish (new)

Parish polygons coloured by median €/m², with a toggle between sale and rent and
a choice of change over 3, 6 or 12 months.

- Polygons come from CAOP, the official administrative map from DGT, simplified
  to keep the file small.
- Each parish shows its median, its n and the change. Parishes under n=8 are
  hatched with no colour.
- Typology filter: all, T1, T2, T3 or larger.
- The change periods depend on history. Our own data gives a 3-month change from
  about 2027-01-30, 6 months from 2027-04-30 and 12 months only from 2027-10-30.
  Until then the 12-month figure comes from INE and is labelled as INE.
- Lisbon rents move with the academic year. The map names the two months it
  compares so a seasonal swing is not read as a trend.

### IM-2 Fair price on every listing (partial)

Rentals have it as `deal_score` in `v_deals`. Extend it to sales and phrase it as
a sentence: "12% abaixo da mediana de T2 em Loures (n=34)".

- Same peer ladder as today: parish, type and typology; then council, type and
  typology; then council and type. Each rung needs n≥8.
- Keeps both comparisons, €/m² and headline price.
- When INE has a median for the parish, the card shows it as a second reference
  ("INE: vendas a 3.410 €/m² no último trimestre").

### IM-3 Listing history (partial)

Price drops and days on market exist (`v_listing_history`, `days_on_market`).
New: catch adverts that were withdrawn and posted again.

- Fingerprint: council, parish, typology, area within 2%, coordinates within
  50 m when both have them, and a fuzzy match on the title. The agency may differ.
- The new advert links to the old one (`relist_of`), inherits its price path, and
  counts days on market from the first appearance in the chain. The portal's
  `listed_at` resets on a relist, so the chain is the honest figure.
- The card reads like "retirado e republicado 2 vezes, 214 dias no total,
  baixou 8%".
- The same matcher delivers the fuzzy deduplication in LIMITATIONS.md, item 2.

### IM-4 Rent versus buy by zone (new)

Gross yield per parish and typology: median annual rent divided by median sale
price, both from €/m² so that flat sizes cancel out. The inverse appears too, as
years of rent that pay the price.

- Needs n≥8 on both sides, otherwise council level.
- Computed from listings and, separately, from INE, shown side by side.
- A ranked table plus a layer on the IM-1 map.
- Labelled as gross. IMI, condomínio and empty months are left out, which makes
  it good for ranking zones and too crude to promise a return.

### IM-5 Real cost of buying (new)

On every sale listing and as a standalone calculator:

- IMT from the tables of the current year, for an own permanent home or a second
  home, with the IMT Jovem exemption as a toggle.
- Imposto do Selo: 0.8% of the price plus 0.6% of the loan.
- Escritura and registo, defaulting to the Casa Pronta fee, editable. Bank fees
  (avaliação, dossier), editable.
- Condomínio from the listing description once DT-4 parses it, otherwise typed
  in.
- IMI estimate. Listings never carry the VPT, so it is estimated as a share of the
  price (adjustable) times the council's IMI rate.
- Mortgage payment from price, down payment, term and an interest rate the users
  set, with a second line at +2 points as a stress test.
- Output: cash needed on signing day, the monthly cost (payment, IMI/12,
  condomínio), and how that compares with renting the same flat at the zone's
  median rent.

Done when the tax tables live in one versioned file with year and source link, a
test fails if the current year has no table (so January never reuses last year's
brackets in silence), and five reference cases match the Portal das Finanças
IMT simulator.

### IM-6 Commute isochrones (partial)

Today there is one destination, by public transport, weekday at 8h, on a 300 m
grid, as minutes per listing.

- One destination per person, each with transit and car. `commute_meta` loses
  its one-row constraint.
- Isochrones at 15, 30 and 45 minutes drawn on the map, traced from the grid.
- Filters on both commutes: both under N minutes, the longer of the two, or the
  sum.
- Car times from r5py over OpenStreetMap ignore traffic and run optimistic at
  rush hour. The page says so. A fixed peak multiplier is a later decision.
- Workplace coordinates stay out of git, as they do now.

### IM-7 Sales coverage (new, depends on Q1)

The crawl takes sale flats only, and 7% of them have coordinates because detail
pages go to rentals first. If buying is a real option: add sale houses to
`DEFAULT_TARGETS`, give sales in the councils that matter a share of the nightly
detail budget, and check the run still fits its window.

## 3. Cars

### CA-0 Source and terms (new, blocks the rest of this section)

Standvirtual is OLX's car portal on the same platform as Imovirtual. On
2026-11-15 its robots.txt allowed `/` and disallowed only account, payment,
ajax and api paths, and search pages carried `__NEXT_DATA__`. Before writing any
code, read Standvirtual's terms of use on automated collection and record the
answer in the README's Sources table. Do the same for Imovirtual, which runs
every night without that check written down anywhere.

- The crawl is scoped by the criteria in Q2. Standvirtual is national and large,
  and at three seconds a page a full pass is out of the question.
- Same layers as property: raw pages, then `cars` and `car_snapshots` with one
  snapshot per car per day.

### CA-1 Price by make, model, year and km (new)

Median price per make, model and year, split into km bands. A depreciation curve
per model plots median price against age, smoothed, and marks the age where the
yearly loss falls under 10% (configurable).

- Needs n≥8 per model and year. Thin points are greyed out.
- The curve is cross-sectional. It compares today's prices of cars of different
  ages, which can differ from what one car loses over the years. Our own
  snapshots add that second view as they pile up.

### CA-2 Cost of ownership over 5 years (new)

Purchase price minus the value at year 5 from CA-1, plus:

- IUC computed from fuel, engine size, CO2 and first registration date. The
  rules change for cars registered from July 2007, and electric cars are exempt.
- Insurance typed in per car, with a default per segment.
- Energy: km per year (typed in) times consumption times price. Fuel prices from
  DGEG's public data, electricity price typed in.
- Maintenance and inspection from an editable table by segment and age.

Output: the total, per month and per km.

### CA-3 Deal score (new)

The asking price against similar cars: same make and model, year within one,
same km band. When that group is thin, a price model on age and km inside the
model takes over. Phrased like IM-2: "9% abaixo de Golf 2019 com 60 a 90 mil km
(n=21)".

### CA-4 Filters (new)

Body type (SUV, carrinha, citadino and the rest of Standvirtual's list, under
Portuguese names), fuel and gearbox, plus price, year and km ranges.

## 4. Deciding together

### DU-1 Shared shortlist with votes (new)

Each person votes gosto, talvez or não on any listing or car. One view shows
where both said gosto, another where they disagree, another what one of them
has not voted on yet.

- A vote follows its item across relists (IM-3).
- Removing an item keeps its votes and notes as history.

### DU-2 Side by side (new)

Two to four items of the same kind. For property the rows are price, €/m², fair
price, history, cost of buying or monthly rent, both commutes, votes and notes.
For cars: price, deal score, km, year, 5-year cost, votes and notes. The best
value in each row is highlighted.

### DU-3 Visit notes and photos (new)

Free text plus a checklist for things listings never say: humidity and its
smell, light, noise, state of kitchen and bathrooms, the building. Photos come
from the phone and are resized on upload.

- Stored on the server outside the public site, served only through the
  authenticated service.
- Part of the nightly backup. Like price history, they cannot be rebuilt.

### DU-4 Alerts (partial)

The Telegram message already announces new rentals that match budget, typology
and councils, and rentals that just dropped.

- Price drop on anything in the shortlist, property or car.
- Saved searches per person that announce new matches.
- Zone alert when a parish median falls more than X% over N weeks, only with
  n≥8 at both ends of the window.
- Each event fires once, as `alerts_sent` already guarantees.

### DU-5 Weekly market diary (new)

Every Monday, a summary of the zones and car models they follow: median moves,
new supply, notable drops, changes on the shortlist. It arrives on Telegram and
stays as a page in the app.

- The numbers come from SQL. The agent may write the prose around them and is
  never the source of a figure.

## 5. Internal agent

### AG-1 Questions in Portuguese, answered from the data (new)

A chat panel in the app, and optionally the Telegram bot, where either person
asks things like:

- "O T2 em Odivelas que vimos sábado está caro?"
- "Onde compensa mais comprar do que arrendar com menos de 40 minutos para os
  dois?"
- "Que anúncios da shortlist baixaram este mês?"
- "Em que idade o Corolla deixa de desvalorizar depressa?"

### AG-2 Tools (new)

- Read-only SQL over the views, through a Postgres role that can SELECT from
  views and nothing else, with a statement timeout and a row cap.
- Domain functions shared with the site: fair price, cost of buying, rent versus
  buy, car cost over 5 years, commute lookup.
- Look up a listing by URL or id. Read the shortlist and the notes.
- Write tools (add to shortlist, create an alert) come later and wait for a
  confirmation click in the app.

### AG-3 Grounding (new)

- Every figure in an answer comes from a tool result in the same conversation.
  The answer gives n and peer group and links each listing it mentions.
- LIMITATIONS.md is part of the agent's instructions, so it says when the data
  has no answer: transaction prices, whether a flat was rented, thin cells.
- Listing titles and descriptions are text written by strangers and are handled
  as data. With a read-only role, a hostile advert has nothing to write to.

### AG-4 Model, cost and privacy (new)

- Claude API through the official Python SDK, model `claude-opus-5-5` by
  default, overridable by an environment variable.
- A monthly spend cap in euros, enforced in code from the `usage` of every
  response. Past the cap the chat says so and stops answering.
- Prompt caching over the stable part: instructions, schema, LIMITATIONS.md.
- A rough guess is 10 to 20 US cents per question with a few tool calls. AG-5
  measures the real figure before the cap is set.
- Questions and query results go to Anthropic's API. Workplace coordinates never
  do, because the commute tools return minutes only.

### AG-5 Evaluation (new)

Twenty to thirty questions whose answers are checked against SQL, run before any
change to prompt, tools or model. A wrong number is a failure however good the
prose reads.

## 6. Data and platform

### DT-1 Sources

| Source | Gives | Status |
|---|---|---|
| Imovirtual | Rent and sale listings | Nightly. Terms of use still to check and record (CA-0) |
| INE | Official medians by parish: sale €/m² every quarter, new-lease rent €/m² every half year | New. Pulled through INE's JSON indicator API, each release stored with its reference period |
| Standvirtual | Car listings | New, after CA-0 |
| Idealista | Listings the other portals lack | Apply for the official API. No scraping: the site blocks robots.txt itself |
| Casa Sapo | A second property source | Later (LIMITATIONS.md, item 3) |
| CAOP (DGT) | Parish polygons | New, static file refreshed once a year |
| DGEG | Fuel prices | New, weekly |

### DT-2 Own history (partial)

`listing_snapshots` keeps one row per listing per day already. Add:

- A nightly aggregate table (day, parish, transaction, type, typology, with n,
  median price and median €/m²), so trends are cheap to query and survive any
  future pruning.
- `car_snapshots` on the same pattern.
- All of it kept forever. Only raw search pages expire, after 60 days.

### DT-3 From a static site to a small service (new)

Votes, notes, photos and the agent need a server, and 20,000 sale listings make
one `data.json` too heavy for a phone browser.

Proposal: one Python web service built from the existing image, as a Deployment
in be-infra `apps/apscrap`. It serves the site, a JSON API that filters on the
server, photo uploads and the agent. Traffic arrives through the Cloudflare
Tunnel and Traefik already in place, behind the same Access application, with a
256 MB memory limit. The nightly CronJob keeps crawling, parsing and alerting.
The Pages site stays up read-only until the service has run for a month.

- The service, the crawler and the agent each get their own database role.
- Votes, notes and photos join the restic backup.

### DT-4 Description parsing (new, unblocks IM-5)

Condomínio, furnished, lease length and guarantor, read from the detail pages
already stored in `raw_pages` (LIMITATIONS.md, item 1).

## 7. Order of work (proposal)

The move is in 2027 and the spring market opens in a few months. History cannot
be backfilled, so baselines go first and the tools for visits soon after.

1. Read the terms of use for CA-0.
2. DT-2 aggregates and the INE import, so every later view has its baseline.
3. DT-3 service with AC-1, ST-1 and the Imóveis screen in the new design,
   reading live data.
4. DU-1 shortlist and DU-3 visit notes, the parts used on visits. Then IM-3,
   IM-4, IM-1 and DU-2.
5. The read-only agent, AG-1 to AG-5.
6. IM-6 for both people, DU-4 and DU-5.
7. Cars, CA-1 to CA-4, on the Carros screen.
8. IM-2 for sales, IM-5 with DT-4, IM-7.

## 8. Decisions

Answered on 2026-11-15:

- **Q1 Rent or buy.** Renting is what they can afford now. The sale crawl keeps
  running unchanged so the history exists if buying comes up later. IM-5 and
  IM-7 stay at the end of the queue, and IM-4 uses sales as context.
- **Q2 The car.** Best value for a young family with a small child. The
  criteria live in Settings (ST-1). Defaults: used, five seats, Isofix,
  family body types (carrinha, SUV, monovolume, compact hatchback), ranked by
  5-year cost per month ahead of price. Insurance defaults in CA-2 assume
  drivers under 25 with a recent licence, which costs far more in Portugal than
  the segment average.
- **Q3 The second person** works elsewhere in Lisbon. Workplace and email are
  set in Settings. The email comes later; until then Access lets one user in.
- **Q6 IMT Jovem.** Both are under 35, so the toggle starts on.

Still open, with the default used until answered:

- **Q4 Telegram.** Default: the existing chat for everything. Settings accepts a
  chat id per person for saved searches.
- **Q5 Agent budget.** Default cap of 20 € a month, editable in Settings.

## 9. Settings

**ST-1 Everything personal is configured in the app** (new). Stored in the
database, never in git:

- Display names and emails of the two users. The email list has to match the
  Access policy, and the page says so.
- One workplace per person: label, address and point on a map.
- Property criteria: budget, typologies, councils, maximum commute. These
  replace the `ALERT_*` environment variables.
- Car criteria: budget, age, km, body types, fuel, gearbox, seats, Isofix, km per
  year, insurance quote.
- Agent spend cap and Telegram chat ids.

The transit grid needs several GB of memory and runs on the laptop. Saving a
new workplace marks its grid as stale. The app then shows "sem estimativa" for
that person until `scripts/commute_grid.py` runs again and the result is loaded.

## 10. Design

The design is the canvas "Casa & Carro, painel a dois" (private, not linked from
this public repository). It has two screens, Imóveis and Carros. Every other
screen follows the same language.

- Colours: background `#F6F2EA`, cards `#FFFDF8` with border `#E4DCCD`, ink
  `#1E1B16`, secondary text `#5C554A`, navy `#22385A` for person A and for
  selection, clay `#C2622D` for person B and highlights, teal `#1F6F6B` for good
  news. The sequential map ramp runs `#E6E9EE`, `#C5CEDB`, `#8FA1BA`, `#4F6A8F`,
  `#22385A`.
- Type: Fraunces for headings and big numbers, Geist for everything else, with
  tabular figures.
- Cards with 16 to 20 px radius, touch targets of at least 44 px, layouts that
  stack at phone width.
- **Imóveis:** summary tiles, the zone map with a venda / arrendamento toggle, a
  zone panel (median, 12-month change and sparkline, rent or sale reference,
  gross yield, days on market, active listings, a one-line reading), cards for
  listings below the median with both votes, and the market diary. It opens on
  arrendamento, since renting is the plan.
- **Carros:** body type chips, summary tiles, median price by model, the
  depreciation curve against the segment, the 5-year cost broken down, and a
  table of listings rated against similar cars.
- **The zone map** is a cartogram: one tile per parish, placed on a grid from the
  parish centroid. The design hand-placed 22 zones; Lisboa, Odivelas and Loures
  have about 38 parishes, so the placement is computed. The Leaflet map stays for
  listings and isochrones (IM-6).
- **Screens the canvas lacks:** Shortlist (DU-1), Comparar (DU-2), Visitas
  (DU-3), Agente (AG-1), Custos (IM-5 and CA-2) and Definições (ST-1).
- Names and initials of the two users come from Settings, never from the code.
- The canvas uses invented numbers. Every figure on the real pages comes from
  the database.

## 11. Status (2026-11-15)

Steps 1 to 7 of section 7 are built. What each requirement needs to count as
done in production, and what still waits on someone:

| Requirement | State |
|---|---|
| CA-0 | Done. Imovirtual: personal use allowed, sharing aggregated data with third parties forbidden (§2.7, 19 Aug 2026); the app is private to two people. Standvirtual: repeated, systematic extraction forbidden (4 May 2026) and database rights claimed, so cars are fetched on demand only. Recorded in the README's Sources table |
| DT-1, DT-2 | Done. INE sales (0012234) and new leases (0014696) by parish, imported weekly; `market_agg_daily` backfilled from every snapshot and refreshed nightly |
| DT-3, AC-1, AC-2, ST-1 | Done. `apscrap serve` in be-infra `apps/apscrap/web.yaml`, on apscrap.beyouweb.com in place of the old static site (which stays on apscrap.pages.dev). Access policy "Casa & Carro"; the second email joins it, and Definições, when it is known |
| IM-1 | Done as a cartogram. 3- and 6-month changes appear once our own history covers them; until then the 12-month change is INE's and says so |
| IM-2 | Done for rent and sale (`v_deals` now covers both) |
| IM-3 | Done. `relist.py` matches reposted adverts; cards and detail show the chain and the days on market across it |
| IM-4 | Done. Gross yield per parish, on the map panel and in the assistant |
| IM-6 | Done. One grid per person and mode (transit and car), filters for both, isochrones on the map. Car times have no traffic model. The second person's grid uses an approximate point until the exact address is set in Definições and the grid is recomputed |
| DU-1, DU-2, DU-3 | Done. Photos are resized on upload and stored in the database, so the nightly backup covers them |
| DU-4 | Done in code: shortlist drops, saved searches, zone alerts. Telegram delivery needs `TELEGRAM_BOT_TOKEN` in production, which has never been set; until then everything shows in the app's diary feed |
| DU-5 | Done. Written on Mondays by the nightly job, with prose from the assistant around the SQL facts |
| AG-1 to AG-4 | Done. OmniRoute (the beyou combo), read-only tools, role `apscrap_readonly`, monthly cap in euros |
| AG-5 | Done: `scripts/agent_eval.py`, 21 questions checked against SQL. First run 16/21; after the fixes it records, 21/21 (about 0.92 € of tokens per run) |
| CA-1 to CA-4 | Done on the Carros screen, for the models the two of you follow |
| IM-5, IM-7 (step 8) | Not started, as decided in Q1 |
