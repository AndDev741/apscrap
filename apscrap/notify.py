"""DU-4 and DU-5: alerts beyond the morning digest, and the weekly diary.

alert.py keeps the morning message (new rentals that fit, and drops). This
module adds, once per event (notifications_sent):
- a drop on anything in the shortlist, property or car;
- new adverts matching a saved search, sent to whoever saved it;
- a zone whose median fell more than X% over N weeks, with n>=8 at both ends.

And on Mondays it writes the week's diary entry: facts from SQL, with a few
sentences from the agent around them when one is configured.

Telegram needs TELEGRAM_BOT_TOKEN. The chat for each person comes from
Settings (telegram.chat_a / chat_b), falling back to TELEGRAM_CHAT_ID.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import os

import httpx

from . import appsettings, listings, market
from .db import connect

log = logging.getLogger(__name__)


def _chats(settings: dict) -> dict:
    default = os.getenv("TELEGRAM_CHAT_ID")
    t = settings.get("telegram") or {}
    return {"a": t.get("chat_a") or default, "b": t.get("chat_b") or default, "all": default}


def _send(chat: str | None, text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not (token and chat):
        return False
    r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                   json={"chat_id": chat, "text": text[:4000], "parse_mode": "HTML",
                         "disable_web_page_preview": True}, timeout=30)
    r.raise_for_status()
    return True


def _fresh(conn, key: str) -> bool:
    """Record the event; True the first time only."""
    return conn.execute(
        "INSERT INTO notifications_sent (key) VALUES (%s) ON CONFLICT DO NOTHING", (key,)).rowcount == 1


def _site() -> str:
    return os.getenv("APSCRAP_APP_URL") or os.getenv("APSCRAP_SITE_URL") or ""


def run(dry_run: bool = False) -> dict:
    s = appsettings.get()
    chats = _chats(s)
    site = _site()
    lines: dict[str, list[str]] = {"a": [], "b": [], "all": []}
    counts = {"shortlist_drops": 0, "saved_search": 0, "zones": 0}

    with connect() as conn:
        # 1. drops on saved adverts (any copy in the chain)
        drops = conn.execute(
            """SELECT c.listing_id, c.new_price, c.prev_price, d.typology, d.parish, d.council, d.transaction_type
                 FROM v_price_changes c
                 JOIN mv_deals d ON d.id = c.listing_id
                WHERE c.direction = 'drop' AND c.changed_at > now() - interval '2 days'
                  AND EXISTS (SELECT 1 FROM shortlist s JOIN listings l ON l.id = s.item_id
                               WHERE s.kind = 'listing' AND s.removed_at IS NULL
                                 AND COALESCE(l.relist_root, l.id) = d.root_id)""").fetchall()
        for d in drops:
            if _fresh(conn, f"sl-drop:listing:{d['listing_id']}:{int(d['new_price'])}"):
                unit = "/mês" if d["transaction_type"] == "rent" else ""
                lines["all"].append(
                    f"📉 Guardado: {d['typology'] or 'imóvel'} em {html.escape(market.short_name(d['parish'] or d['council']))} "
                    f"baixou de {market.fmt_eur(d['prev_price'])} para {market.fmt_eur(d['new_price'])}{unit}"
                    + (f' · <a href="{site}#/anuncio/{d["listing_id"]}">abrir</a>' if site else ""))
                counts["shortlist_drops"] += 1
        if conn.execute("SELECT to_regclass('car_snapshots') IS NOT NULL AS ok").fetchone()["ok"]:
            car_drops = conn.execute(
                """WITH last2 AS (
                       SELECT car_id, price_eur, observed_date,
                              lag(price_eur) OVER (PARTITION BY car_id ORDER BY observed_date) AS prev
                         FROM car_snapshots)
                   SELECT l.car_id, l.price_eur, l.prev, c.title
                     FROM last2 l JOIN cars c ON c.id = l.car_id
                    WHERE l.prev IS NOT NULL AND l.price_eur < l.prev
                      AND l.observed_date > current_date - 2
                      AND EXISTS (SELECT 1 FROM shortlist s WHERE s.kind = 'car' AND s.item_id = l.car_id
                                    AND s.removed_at IS NULL)""").fetchall()
            for c in car_drops:
                if _fresh(conn, f"sl-drop:car:{c['car_id']}:{int(c['price_eur'])}"):
                    lines["all"].append(f"🚗 Guardado: {html.escape((c['title'] or 'carro')[:60])} baixou para "
                                        f"{market.fmt_eur(c['price_eur'])}")
                    counts["shortlist_drops"] += 1

        # 2. saved searches
        searches = conn.execute(
            """SELECT s.*, u.slot FROM saved_searches s LEFT JOIN app_users u ON u.id = s.user_id
                WHERE s.kind = 'listing'""").fetchall()
        councils = s["property_criteria"].get("councils") or market.DEFAULT_COUNCILS
        for ss in searches:
            f = dict(ss["filters"] or {})
            f["councils"] = councils
            f["new_days"] = 2
            f["sort"] = "discount"
            found = listings.search(f, 30, conn=conn)["items"]
            new = [r for r in found if _fresh(conn, f"ss:{ss['id']}:{r['id']}")]
            if new:
                who = ss["slot"] or "all"
                lines[who].append(f"🔎 <b>{html.escape(ss['name'])}</b>: {len(new)} novos")
                for r in new[:5]:
                    lines[who].append(f"  {market.fmt_eur(r['price_eur'])} · {r['typology'] or ''} · "
                                      f"{html.escape(market.short_name(r['parish'] or r['council']))}"
                                      + (f' · <a href="{site}#/anuncio/{r["id"]}">abrir</a>' if site else ""))
                counts["saved_search"] += len(new)

        # 3. zones that fell
        za = s.get("zone_alert") or {}
        pct, weeks = float(za.get("drop_pct") or 3), int(za.get("weeks") or 4)
        mode = s["property_criteria"].get("mode") or "rent"
        moves = conn.execute(
            """WITH a AS (
                   SELECT council, parish,
                          percentile_cont(0.5) WITHIN GROUP (ORDER BY median_ppm)
                              FILTER (WHERE day > current_date - 7) AS now_ppm,
                          percentile_cont(0.5) WITHIN GROUP (ORDER BY median_ppm)
                              FILTER (WHERE day BETWEEN current_date - %(d)s - 7 AND current_date - %(d)s) AS then_ppm
                     FROM market_agg_daily
                    WHERE transaction_type = %(m)s AND property_type = 'flat' AND typology = ''
                      AND parish <> '' AND n >= 8 AND council = ANY(%(c)s)
                    GROUP BY 1, 2)
               SELECT * FROM a WHERE now_ppm IS NOT NULL AND then_ppm IS NOT NULL
                 AND now_ppm < then_ppm * (1 - %(p)s / 100.0)""",
            {"d": weeks * 7, "m": mode, "c": councils, "p": pct}).fetchall()
        week = dt.date.today().isocalendar()
        for z in moves:
            if _fresh(conn, f"zone:{z['council']}:{z['parish']}:{week[0]}-{week[1]}"):
                drop = (1 - z["now_ppm"] / z["then_ppm"]) * 100
                lines["all"].append(f"🗺️ {html.escape(market.short_name(z['parish']))}: mediana desceu {drop:.1f}% "
                                    f"em {weeks} semanas")
                counts["zones"] += 1

        sent = False
        for who, ls in lines.items():
            if not ls:
                continue
            text = "\n".join(ls)
            if dry_run:
                print(f"[{who}]\n{text}\n")
                continue
            sent |= _send(chats.get(who), text)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
    return counts | {"sent": sent}


# -- DU-5 ----------------------------------------------------------------------

def week_start(today: dt.date | None = None) -> dt.date:
    today = today or dt.date.today()
    return today - dt.timedelta(days=today.weekday())


def diary_facts(conn, settings: dict) -> dict:
    councils = settings["property_criteria"].get("councils") or market.DEFAULT_COUNCILS
    mode = settings["property_criteria"].get("mode") or "rent"
    cm = conn.execute(
        """SELECT council,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY median_ppm) FILTER (WHERE day > current_date - 7) AS now_ppm,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY median_ppm)
                      FILTER (WHERE day BETWEEN current_date - 14 AND current_date - 8) AS before_ppm,
                  max(n) FILTER (WHERE day > current_date - 7) AS n
             FROM market_agg_daily
            WHERE transaction_type = %s AND property_type = 'flat' AND typology = '' AND parish = ''
              AND council = ANY(%s) AND day > current_date - 15
            GROUP BY council""", (mode, councils)).fetchall()
    budget = settings["property_criteria"].get("max_price")
    new = conn.execute(
        """SELECT count(*) AS n, count(*) FILTER (WHERE price_eur <= %s) AS in_budget,
                  count(*) FILTER (WHERE discount_pct > 5) AS below
             FROM mv_deals WHERE transaction_type = %s AND council = ANY(%s) AND NOT is_midstay
              AND NOT is_backlog AND first_seen_at > now() - interval '7 days'""", (budget or 1e12, mode, councils)).fetchone()
    drops = conn.execute(
        """SELECT count(*) AS n FROM v_price_changes c JOIN mv_deals d ON d.id = c.listing_id
            WHERE c.direction = 'drop' AND c.changed_at > now() - interval '7 days'
              AND d.transaction_type = %s AND d.council = ANY(%s) AND NOT d.is_midstay""", (mode, councils)).fetchone()
    sl = conn.execute(
        """SELECT count(*) AS saved,
                  count(*) FILTER (WHERE vote_a = 'gosto' AND vote_b = 'gosto') AS agree
             FROM v_shortlist WHERE kind = 'listing'""").fetchone()
    ine = conn.execute("SELECT indicator, max(period_end) AS last FROM ine_values GROUP BY 1").fetchall()
    return {
        "semana": str(week_start()), "modo": mode,
        "concelhos": [{"concelho": r["council"], "mediana_eur_m2": round(float(r["now_ppm"]), 2) if r["now_ppm"] else None,
                       "semana_anterior_eur_m2": round(float(r["before_ppm"]), 2) if r["before_ppm"] else None,
                       "n": r["n"]} for r in cm],
        "novos_7d": new["n"], "novos_no_orcamento": new["in_budget"], "novos_abaixo_mediana": new["below"],
        "baixas_7d": drops["n"], "shortlist": {"guardados": sl["saved"], "de_acordo": sl["agree"]},
        "ine_ultimo_periodo": {r["indicator"]: str(r["last"]) for r in ine},
    }


def write_diary(force: bool = False) -> dict:
    from . import agent
    s = appsettings.get()
    ws = week_start()
    with connect() as conn:
        exists = conn.execute("SELECT 1 FROM diary WHERE week_start = %s", (ws,)).fetchone()
        if exists and not force:
            return {"skipped": "already written", "week": str(ws)}
        facts = diary_facts(conn, s)
    prose = None
    try:
        prose = agent.narrate(facts)
    except Exception:  # noqa: BLE001 - the facts stand on their own
        log.exception("diary narration failed")
    with connect() as conn:
        conn.execute(
            """INSERT INTO diary (week_start, facts, prose) VALUES (%s, %s, %s)
               ON CONFLICT (week_start) DO UPDATE SET facts = EXCLUDED.facts, prose = EXCLUDED.prose,
                   created_at = now()""",
            (ws, __import__("psycopg").types.json.Jsonb(facts), prose))
        conn.commit()
    text = f"📰 <b>Diário da semana de {ws:%d/%m}</b>\n" + (html.escape(prose) if prose else
            f"{facts['novos_7d']} anúncios novos, {facts['baixas_7d']} baixas de preço.")
    if _site():
        text += f'\n<a href="{_site()}">Abrir a app</a>'
    sent = False
    try:
        sent = _send(_chats(s)["all"], text)
    except Exception:  # noqa: BLE001
        log.exception("diary telegram failed")
    return {"week": str(ws), "prose": bool(prose), "sent": sent}
