"""Morning Telegram message: rentals that match what you are looking for and
appeared since yesterday, plus the ones that just got cheaper.

Criteria come from the environment so they can change without a new image:

    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   required
    ALERT_MAX_PRICE        1300
    ALERT_TYPOLOGIES       T1,T2,T3,T4,T5,T6
    ALERT_COUNCILS         Lisboa,Odivelas,Loures
    ALERT_MAX_COMMUTE      minutes, unset = no limit (listings with no commute
                           estimate yet still pass)
    APSCRAP_SITE_URL       linked at the bottom of the message
"""
import html
import logging
import os

import httpx

from .db import connect

log = logging.getLogger(__name__)

MAX_ITEMS = 12          # per section; the site has the rest
TELEGRAM_LIMIT = 4096   # characters per message


def configured() -> bool:
    return bool(os.getenv("TELEGRAM_BOT_TOKEN") and os.getenv("TELEGRAM_CHAT_ID"))


def _list(name: str, default: str) -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]


def _criteria() -> tuple[str, dict]:
    commute = os.getenv("ALERT_MAX_COMMUTE")
    params = {
        "max_price": float(os.getenv("ALERT_MAX_PRICE", "1300")),
        "typologies": _list("ALERT_TYPOLOGIES", "T1,T2,T3,T4,T5,T6"),
        "councils": _list("ALERT_COUNCILS", "Lisboa,Odivelas,Loures"),
        "max_commute": int(commute) if commute else None,
    }
    where = """
        price_eur <= %(max_price)s
        AND typology = ANY(%(typologies)s)
        AND council = ANY(%(councils)s)
        AND NOT is_temporary
        AND (%(max_commute)s::int IS NULL OR commute_min IS NULL OR commute_min <= %(max_commute)s::int)
    """
    return where, params


def _fmt(r: dict, extra: str = "") -> str:
    bits = [f"<b>€{r['price_eur']:,.0f}</b>".replace(",", "."),
            r["typology"] or "?",
            f"{r['area_sqm']:.0f} m²" if r["area_sqm"] else None,
            html.escape(r["parish"] or r["council"] or "")]
    if r["commute_min"] is not None:
        approx = "~" if r["geo_precision"] != "exact" else ""
        bits.append(f"🚇 {approx}{r['commute_min']} min")
    if r["deal_score"] is not None and r["deal_score"] >= 10:
        bits.append(f"{r['deal_score']:.0f}% abaixo")
    line = " · ".join(b for b in bits if b)
    return f'{line}{extra}\n<a href="{html.escape(r["url"])}">{html.escape((r["title"] or "anúncio")[:70])}</a>'


def _send(text: str) -> None:
    token, chat = os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    # Split on blank lines so no listing is cut in half.
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) + 2 > TELEGRAM_LIMIT:
            chunks.append(cur)
            cur = ""
        cur = f"{cur}\n\n{block}" if cur else block
    chunks.append(cur)
    for chunk in chunks:
        r = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": chunk, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=30,
        )
        r.raise_for_status()


def run(dry_run: bool = False) -> dict:
    where, params = _criteria()
    site = os.getenv("APSCRAP_SITE_URL")

    with connect() as conn:
        first_run = conn.execute("SELECT NOT EXISTS (SELECT 1 FROM alerts_sent) AS empty").fetchone()["empty"]

        new = conn.execute(
            f"""SELECT * FROM v_deals d
                 WHERE {where}
                   AND first_seen_at > now() - interval '36 hours'
                   AND NOT EXISTS (SELECT 1 FROM alerts_sent a
                                    WHERE a.listing_id = d.id AND a.kind = 'new')
                 ORDER BY deal_score DESC NULLS LAST""",
            params,
        ).fetchall()
        drops = conn.execute(
            f"""SELECT * FROM v_deals d
                 WHERE {where}
                   AND last_drop_on >= current_date - 1
                   AND NOT EXISTS (SELECT 1 FROM alerts_sent a
                                    WHERE a.listing_id = d.id
                                      AND a.kind = 'drop:' || d.price_eur::int)
                 ORDER BY deal_score DESC NULLS LAST""",
            params,
        ).fetchall()

        if first_run:
            # Everything is "new" the first night. Record it all as already
            # announced and send one line, instead of 1,500 listings at once.
            text = (f"🏠 Alerta do apScrap ligado. Hoje {len(new)} anúncios batem com os teus "
                    f"critérios (até €{params['max_price']:.0f}, {', '.join(params['councils'])}). "
                    "A partir de amanhã só chegam os novos e os que baixarem de preço.")
            if site:
                text += f'\n\n<a href="{html.escape(site)}">Ver todos</a>'
        elif not new and not drops:
            log.info("nothing new for the alert")
            return {"new": 0, "drops": 0, "sent": False}
        else:
            parts = []
            if new:
                head = f"🆕 <b>{len(new)} novos</b>" + (f" (os {MAX_ITEMS} melhores)" if len(new) > MAX_ITEMS else "")
                parts.append(head)
                parts += [_fmt(r) for r in new[:MAX_ITEMS]]
            if drops:
                head = f"📉 <b>{len(drops)} baixaram de preço</b>"
                parts.append(head)
                parts += [_fmt(r, f" (era €{r['max_price']:,.0f})".replace(",", "."))
                          for r in drops[:MAX_ITEMS]]
            if site:
                parts.append(f'<a href="{html.escape(site)}">Abrir o apScrap</a>')
            text = "\n\n".join(parts)

        if dry_run:
            print(text)
            return {"new": len(new), "drops": len(drops), "sent": False, "first_run": first_run}

        _send(text)
        conn.execute(
            """INSERT INTO alerts_sent (listing_id, kind)
               SELECT x, 'new' FROM unnest(%s::bigint[]) x
               ON CONFLICT DO NOTHING""",
            ([r["id"] for r in new],),
        )
        conn.execute(
            """INSERT INTO alerts_sent (listing_id, kind)
               SELECT i, 'drop:' || p FROM unnest(%s::bigint[], %s::int[]) AS t(i, p)
               ON CONFLICT DO NOTHING""",
            ([r["id"] for r in drops], [int(r["price_eur"]) for r in drops]),
        )
        conn.commit()

    return {"new": len(new), "drops": len(drops), "sent": True, "first_run": first_run}
