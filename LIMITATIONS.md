# What this data answers, and what it does not

Numbers below were measured on the test dataset of 219 Lisboa adverts collected
2026-09-15. A full nightly crawl covers roughly 4,400 rentals and 16,500 sales,
so sample sizes improve a lot. The biases do not.

## Answers well

- **Asking price by parish, typology and area.** Median, quartiles and €/m²,
  which is the number that genuinely compares neighbourhoods.
- **Supply.** How many T2s under €1,500 exist in each parish, and how that count
  moves week to week.
- **How long adverts sit.** `days_on_market` comes from the portal's own
  first-published date. A place advertised for 200 days has a landlord with a
  different attitude to negotiation than one listed on Tuesday.
- **Price movements.** Every change, with size, direction and date. This needs
  two days of history minimum and gets more useful every week.
- **Who is advertising.** 73% agency and 27% private owner in the sample.
- **Geography**, once detail enrichment has filled in coordinates.

## Does not answer

**Asking price is not transaction price.** Nothing here sees what anyone
actually paid. The gap moves with market temperature and is invisible from
listing data. Portugal's INE publishes real transaction statistics, and joining
against those is the honest way to calibrate.

**A listing disappearing does not mean it rented.** It might have rented, or the
agency mandate lapsed, or the owner gave up, or it moved to Alojamento Local.
So `days_on_market` really means days advertised, and a true absorption rate is
not computable from this. ERA, KW and RE/MAX are the exception: they keep let
flats online with a flag, and those leave with `gone_reason = 'rented'`. The
rest leave as `missing`, 60 hours after their portal last showed them.

**Duplicates are folded, not perfectly.** `dedup.py` links the same flat on two
portals when price, area, place and typology agree, more strictly when the
advertisers differ. Two identical flats in one building at the same price can
still be merged, and a copy with a different area or a stale price is missed.
Copies of one flat on the same portal at the same time are not caught at all.

**Mid-stay platforms are a different market.** Uniplaces, Spacest and UKIO
(furnished, by the month) are left out of every median by agency name, plus
titles that say "média duração". A smaller platform with another name would
slip through until it is added to `is_midstay()` in sql/001.

**Price moves under 1% are ignored** (0.5% for sales). An agency that lowers
the rent by €5 a week for a month moves it 2% without a single counted drop.

**Bait listings exist.** Agencies post attractive adverts to harvest leads for
properties that are not available. The fraction is unknown and unmeasurable from
the outside.

**Thin cells are noise.** In the test data 109 of 114 parish-by-typology
combinations had three or four listings. A median over n=3 tells you very little.
Filter on `n_listings` before believing any cell, and prefer council level when
the parish is quiet.

**Five portals, still a slice of the market.** Imovirtual skews toward agency
inventory; Casa Sapo adds private owners, RE/MAX, ERA and KW their own stock.
Private rentals that move through word of mouth, Facebook groups and university
noticeboards never appear, and neither do Idealista-exclusive listings, since
that portal blocks scrapers. The other portals are crawled for rentals in the
councils in Definições only; sales stay Imovirtual-only.

**Costs beyond the headline number are missing.** Condomínio fees, IMI, IMT and
stamp duty on a purchase, agency commission, and how many months upfront a
landlord wants. Deposit is captured when the advertiser filled it in. The rest
usually lives in free-text description.

**Contract terms are invisible.** Lease length, furnished status, pets, whether a
guarantor is required. All of it sits in prose that nothing here parses.

**Nothing about whether a place is nice.** Light, noise, the stairs, the
neighbours, the state of the building. `energy_cert` and `build_year` are the
only quality proxies and they are weak ones.

**The commute is an estimate.** It is the median public-transit time on a
weekday at 8h from a 300 m grid cell, routed over the AML timetables. A listing
without its own coordinates yet (no detail page fetched) borrows the centre of
its parish, which the site marks with "~". Timetable feeds go stale: re-run
`scripts/commute_grid.py` a few times a year. The Carris Metropolitana feed
ships trips with unknown service ids, which are dropped before routing.

**History only runs forward.** There is zero data from before the first crawl.
Any trend claim needs months of collection, and Lisbon rentals have strong
seasonality that a short window will misread as a trend.

**The deal score is relative.** "30% abaixo" means below the median of similar
adverts nearby, which are themselves asking prices. A whole parish can be
overpriced and every listing in it can still look like a deal next to its
neighbours.

**Cars are a thin slice by design.** Standvirtual's terms forbid repeated,
systematic extraction, so only the models you follow are fetched, when you ask,
at most weekly, up to five pages each. Depreciation curves are cross-sectional
(today's prices of cars of different ages) and need at least 8 adverts per
model. Insurance and maintenance are estimates until you type in your own.

**The assistant can only read, and can still be wrong.** Every number it gives
should come from a tool call, and `scripts/agent_eval.py` checks a set of them
against SQL. A model behind a gateway can still misread a question; the answer
always names its source so you can check.

## Worth adding, roughly in order of value

1. **Description parsing.** Condomínio, furnished, lease length and guarantor
   requirements are all sitting in detail pages already stored in `raw_pages`.
2. **Description similarity in dedup.** Every portal but Imovirtual now stores
   the description; comparing them would catch copies whose area or price
   drifted.
3. **Idealista's official API**, if the application is accepted.
