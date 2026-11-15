"""Five-year cost of owning a used car in Portugal (CA-2).

Tax tables are the Código do IUC as consolidated by the tax authority in
August 2026 (rates of Lei 82/2023, kept unchanged by the 2026 budget) and the
2026 inspection fee (Deliberação 1598-A/2025). They change by law, so each one
carries the year it is valid for; `check_tables()` fails once the calendar
moves past them, and the nightly job logs that loudly.

Insurance, maintenance and consumption are estimates. Settings (ST-1) can
override every one of them, and the page says which numbers are estimates.
"""
from __future__ import annotations

import datetime as dt
import logging
import math

import httpx

log = logging.getLogger(__name__)

TABLES_YEAR = 2026

# (upper bound inclusive, euros); None = no upper bound
IUC_B = {
    "cc": [(1250, 31.77), (1750, 63.74), (2500, 127.35), (None, 435.84)],
    "co2_nedc": [(120, 65.15), (180, 97.63), (250, 212.04), (None, 363.25)],
    "co2_wltp": [(140, 65.15), (205, 97.63), (260, 212.04), (None, 363.25)],
    # only when first registered (PT or EU/EEA) after 2017-01-01
    "co2_extra_nedc": [(180, 0.0), (250, 31.77), (None, 63.74)],
    "co2_extra_wltp": [(205, 0.0), (260, 31.77), (None, 63.74)],
    "diesel_extra": [(1250, 5.02), (1750, 10.07), (2500, 20.12), (None, 68.85)],
}
IUC_B_COEF = {2007: 1.00, 2008: 1.05, 2009: 1.10}   # 2010 onwards: 1.15

# Category A: registered 1981 to 30 June 2007. Columns: after 1995, 1990-1995, 1981-1989.
IUC_A_ROWS = [(19.90, 12.55, 8.80), (39.95, 22.45, 12.55), (62.40, 34.87, 17.49),
              (158.31, 83.49, 36.09), (287.49, 156.54, 79.72), (512.23, 263.11, 120.90)]
IUC_A_PETROL_CC = [1000, 1300, 1750, 2600, 3500, None]
IUC_A_OTHER_CC = [1500, 2000, 3000, None]
IUC_A_DIESEL_EXTRA = [(1500, (3.14, 1.98, 1.39)), (2000, (6.31, 3.55, 1.98)),
                      (3000, (9.86, 5.51, 2.76)), (None, (25.01, 13.19, 5.70))]

IPO = {"fee": round(30.46 * 1.23, 2), "first_at_age": 4, "biennial_until_age": 8}

# Typical CO2 (WLTP g/km) and engine size when the advert does not say. Used
# only to place the car in an IUC bracket, and the page marks it as estimated.
DEFAULT_CO2 = {"gasolina": 135, "gasoleo": 125, "hibrido": 105, "hibrido_plugin": 30,
               "gpl": 125, "eletrico": 0}
DEFAULT_CC = {"gasolina": 1200, "gasoleo": 1500, "hibrido": 1800, "hibrido_plugin": 1500,
              "gpl": 1000, "eletrico": 0}

DEFAULTS = {
    "km_per_year": 12000,
    # litres (or kWh for electric) per 100 km
    "consumption": {"gasolina": 6.5, "gasoleo": 5.3, "hibrido": 4.8, "hibrido_plugin": 3.0,
                    "gpl": 8.5, "eletrico": 16.5},
    # plug-in hybrids: share of km on electricity when charged at home
    "plugin_electric_share": 0.5,
    "plugin_kwh_per_100": 18.0,
    "electricity_eur_kwh": 0.20,
    # Young drivers (under 25, recent licence) pay well above the segment
    # average. These are rough annual premiums for third-party plus glass and
    # theft; replace them with a real quote in Settings.
    "insurance": {"citadino": 750, "utilitario": 800, "carrinha": 900, "suv": 950,
                  "monovolume": 900, "sedan": 950, "coupe": 1300, "cabrio": 1300, "outro": 900},
    # Servicing and tyres per year, before and after the car is 6 years old.
    "maintenance": {"combustao": (450, 650), "hibrido": (380, 550), "eletrico": (250, 380)},
    # Value lost per year when the model has too few adverts to fit a curve.
    "depreciation_k": {"gasolina": 0.11, "gasoleo": 0.11, "hibrido": 0.085,
                       "hibrido_plugin": 0.12, "gpl": 0.10, "eletrico": 0.15},
    "years": 5,
}

# Fallback fuel prices (DGEG national average, 14 Nov 2026) for when the API
# is unreachable and nothing is cached.
FUEL_FALLBACK = {"gasolina": 1.908, "gasoleo": 2.121, "gpl": 1.029}
DGEG_IDS = {"gasolina": 3201, "gasoleo": 2101, "gpl": 1120}


def check_tables(today: dt.date | None = None) -> None:
    today = today or dt.date.today()
    if today.year > TABLES_YEAR:
        raise RuntimeError(
            f"carcost tax tables are for {TABLES_YEAR}; update IUC and IPO for {today.year}")


def _bracket(table, value):
    for upper, euros in table:
        if upper is None or value <= upper:
            return euros
    return table[-1][1]


def iuc(fuel: str | None, engine_cc: int | None, co2_gkm: int | None,
        first_registration: dt.date | None) -> dict:
    """Annual IUC and how it was reached. Electric cars are exempt."""
    fuel = fuel or "gasolina"
    if fuel == "eletrico":
        return {"eur": 0.0, "note": "isento (elétrico)"}
    reg = first_registration or dt.date(dt.date.today().year - 4, 6, 1)
    cc = engine_cc or DEFAULT_CC.get(fuel, 1400)
    estimated = engine_cc is None or co2_gkm is None
    diesel = fuel == "gasoleo"

    if reg < dt.date(2007, 7, 1):
        rows = IUC_A_PETROL_CC if fuel in ("gasolina", "hibrido", "hibrido_plugin") else IUC_A_OTHER_CC
        idx = next(i for i, ub in enumerate(rows) if ub is None or cc <= ub)
        col = 0 if reg.year > 1995 else (1 if reg.year >= 1990 else 2)
        eur = IUC_A_ROWS[idx][col]
        if diesel:
            eur += next(v for ub, v in IUC_A_DIESEL_EXTRA if ub is None or cc <= ub)[col]
        return {"eur": round(max(eur, 0), 2), "category": "A", "estimated": estimated}

    co2 = co2_gkm if co2_gkm is not None else DEFAULT_CO2.get(fuel, 130)
    # WLTP values on certificates from September 2018; NEDC before.
    test = "wltp" if reg >= dt.date(2018, 9, 1) else "nedc"
    eur = _bracket(IUC_B["cc"], cc) + _bracket(IUC_B[f"co2_{test}"], co2)
    if reg > dt.date(2017, 1, 1):
        eur += _bracket(IUC_B[f"co2_extra_{test}"], co2)
    eur *= IUC_B_COEF.get(reg.year, 1.15)
    if diesel:
        eur += _bracket(IUC_B["diesel_extra"], cc)
    return {"eur": round(eur, 2), "category": "B", "co2_test": test, "estimated": estimated}


def inspections(age_now: float, years: int) -> int:
    """How many periodic inspections fall in the next `years` years."""
    n = 0
    for k in range(1, years + 1):
        age = math.floor(age_now) + k
        if age < IPO["first_at_age"]:
            continue
        if age <= IPO["biennial_until_age"]:
            n += (age - IPO["first_at_age"]) % 2 == 0
        else:
            n += 1
    return n


def fuel_prices(settings: dict | None = None) -> dict:
    cached = (settings or {}).get("fuel_prices") or {}
    return {k: cached.get(k, v) for k, v in FUEL_FALLBACK.items()}


def fetch_fuel_prices() -> dict:
    """National average from DGEG's open (undocumented) API, last 7 days."""
    today = dt.date.today()
    ids = ",".join(str(v) for v in DGEG_IDS.values())
    url = ("https://precoscombustiveis.dgeg.gov.pt/api/PrecoComb/PMD"
           f"?idsTiposComb={ids}&dataIni={today - dt.timedelta(days=7)}&dataFim={today}"
           "&qtdPorPagina=21&pagina=1&orderDesc=1")
    data = httpx.get(url, timeout=20).json()
    names = {"Gasolina simples 95": "gasolina", "Gasóleo simples": "gasoleo", "GPL Auto": "gpl"}
    out, day = {}, None
    for row in data.get("resultado") or []:
        key = names.get(row.get("TipoCombustivel"))
        if key and key not in out:
            out[key] = float(row["PrecoMedio"].replace("€", "").replace(",", ".").strip())
            day = day or row.get("Data")
    if len(out) < 3:
        raise ValueError(f"DGEG answered without every fuel: {out}")
    out["date"] = day
    return out


def _segment_key(fuel: str) -> str:
    if fuel == "eletrico":
        return "eletrico"
    if fuel in ("hibrido", "hibrido_plugin"):
        return "hibrido"
    return "combustao"


def tco(car: dict, settings: dict | None = None, k: float | None = None) -> dict:
    """Five years of owning `car` (a v_car_current row or similar).

    car needs price_eur, fuel, body_type, age_years and optionally engine_cc,
    co2_gkm, first_registration. k is the model's fitted yearly value loss
    (cars.depreciation); without it a default per fuel applies."""
    s = settings or {}
    crit = s.get("car_criteria") or {}
    years = DEFAULTS["years"]
    fuel = car.get("fuel") or "gasolina"
    body = car.get("body_type") or "outro"
    price = float(car.get("price_eur") or 0)
    age = float(car.get("age_years") or 4)
    km_year = float(crit.get("km_per_year") or DEFAULTS["km_per_year"])
    prices = fuel_prices(s)
    estimates = []

    kk = k if k is not None else DEFAULTS["depreciation_k"].get(fuel, 0.11)
    if k is None:
        estimates.append("desvalorização média do combustível")
    value_end = price * math.exp(-kk * years)
    depreciation = price - value_end

    cons = (crit.get("consumption") or {}).get(fuel) or DEFAULTS["consumption"].get(fuel, 6.5)
    elec = float(crit.get("electricity_eur_kwh") or DEFAULTS["electricity_eur_kwh"])
    if fuel == "eletrico":
        energy_year = km_year / 100 * cons * elec
    elif fuel == "hibrido_plugin":
        share = DEFAULTS["plugin_electric_share"]
        energy_year = (km_year * share / 100 * DEFAULTS["plugin_kwh_per_100"] * elec
                       + km_year * (1 - share) / 100 * 5.5 * prices["gasolina"])
    else:
        litre = prices["gpl"] if fuel == "gpl" else prices["gasoleo" if fuel == "gasoleo" else "gasolina"]
        energy_year = km_year / 100 * cons * litre

    quote = crit.get("insurance_eur")
    insurance_year = float(quote) if quote else DEFAULTS["insurance"].get(body, 900)
    if not quote:
        estimates.append("seguro para condutores jovens")
    if fuel == "eletrico" and not quote:
        insurance_year *= 1.1

    lo, hi = DEFAULTS["maintenance"][_segment_key(fuel)]
    maintenance = sum(lo if age + i < 6 else hi for i in range(years))

    reg = car.get("first_registration")
    if isinstance(reg, str):
        reg = dt.date.fromisoformat(reg[:10])
    if reg is None and car.get("year"):
        reg = dt.date(int(car["year"]), 6, 1)
    tax = iuc(fuel, car.get("engine_cc"), car.get("co2_gkm"), reg)
    if tax.get("estimated"):
        estimates.append("IUC com cilindrada/CO2 típicos")
    ipo = inspections(age, years) * IPO["fee"]

    parts = [
        {"key": "desvalorizacao", "nome": "Desvalorização", "eur": round(depreciation)},
        {"key": "energia", "nome": "Energia", "eur": round(energy_year * years)},
        {"key": "seguro", "nome": "Seguro", "eur": round(insurance_year * years)},
        {"key": "manutencao", "nome": "Manutenção", "eur": round(maintenance + ipo)},
        {"key": "iuc", "nome": "IUC", "eur": round(tax["eur"] * years)},
    ]
    total = sum(p["eur"] for p in parts)
    return {
        "parts": parts,
        "total": total,
        "per_month": round(total / (years * 12)),
        "per_km": round(total / (km_year * years), 3),
        "value_end": round(value_end),
        "retention_5y": round(math.exp(-kk * years), 3),
        "iuc_year": tax["eur"],
        "km_per_year": km_year,
        "estimates": estimates,
    }
