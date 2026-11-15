#!/usr/bin/env python3
"""AG-5: check the assistant's numbers against SQL.

Each case is a question in Portuguese and the figure the answer must contain,
computed here straight from the database at run time, so the expected values
follow the data. A case passes when some number in the answer is within the
tolerance of the expected one. A wrong number is a failure however good the
prose reads. Refusal cases pass when the answer says the data does not have it.

Run before changing the prompt, the tools or the model. It spends real tokens
(about 20 questions, a few cents each):

    LLM_BASE_URL=.. LLM_API_KEY=.. LLM_MODEL=.. python scripts/agent_eval.py [--only 3,7] [--out results.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apscrap import agent, appsettings, carcost, market  # noqa: E402
from apscrap.db import connect  # noqa: E402


def one(sql, params=()):
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return None if row is None else list(row.values())[0]


def median_ppm(mode, council, parish=None, typ=None, ptype="flat"):
    return one("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY price_per_sqm) FROM mv_deals
                   WHERE transaction_type = %s AND council = %s AND property_type = %s
                     AND (%s::text IS NULL OR parish = %s) AND (%s::text IS NULL OR typology = %s)""",
               (mode, council, ptype, parish, parish, typ, typ))


def cases() -> list[dict]:
    zones = {z["parish"]: z for z in market.zones("rent")["zones"]}
    top_yield = max((z for z in zones.values() if z["gross_yield"]), key=lambda z: z["gross_yield"])
    sample = one("""SELECT id FROM mv_deals WHERE transaction_type = 'rent' AND council = 'Lisboa'
                     AND discount_pct IS NOT NULL AND geo_precision = 'exact' ORDER BY id LIMIT 1""")
    sample_disc = one("SELECT discount_pct FROM mv_deals WHERE id = %s", (sample,))
    from apscrap import listings
    sample_commute = (listings.detail(sample)["commute"] or {}).get("a_transit")
    hybrid = carcost.tco({"price_eur": 20000, "fuel": "hibrido", "body_type": "carrinha", "age_years": 4},
                         appsettings.get())["total"]
    out = [
        {"q": "Qual é a renda mediana por m² dos apartamentos em Lisboa?", "expect": median_ppm("rent", "Lisboa"), "tol": 0.02},
        {"q": "Qual é a renda mediana por m² dos apartamentos em Odivelas?", "expect": median_ppm("rent", "Odivelas"), "tol": 0.02},
        {"q": "Quanto custa em mediana o m² de um apartamento à venda em Loures?", "expect": median_ppm("sale", "Loures"), "tol": 0.02},
        {"q": "Quantos anúncios de arrendamento ativos há agora em Odivelas?",
         "expect": one("SELECT count(*) FROM mv_deals WHERE transaction_type = 'rent' AND council = 'Odivelas'"), "tol": 0},
        {"q": "Qual é a renda mensal mediana de um T2 em Arroios?",
         "expect": one("""SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY price_eur) FROM mv_deals
                           WHERE transaction_type = 'rent' AND parish = 'Arroios' AND typology = 'T2'"""), "tol": 0.02},
        {"q": "Que freguesia tem o rendimento bruto mais alto, e qual é?", "expect": top_yield["gross_yield"], "tol": 0.03,
         "also": top_yield["parish"].split(" e ")[0].split(",")[0]},
        {"q": "Quantas baixas de preço houve nos últimos 7 dias em anúncios de arrendamento nos nossos concelhos?",
         "expect": one("""SELECT count(*) FROM v_price_changes c JOIN mv_deals d ON d.id = c.listing_id
                           WHERE c.direction = 'drop' AND c.changed_at > now() - interval '7 days'
                             AND d.transaction_type = 'rent' AND d.council IN ('Lisboa', 'Odivelas', 'Loures')"""), "tol": 0.03},
        {"q": "Qual é a mediana oficial do INE para o preço de venda por m² em Alvalade?",
         "expect": one("""SELECT value FROM ine_values WHERE indicator = 'sale_ppm' AND parish = 'Alvalade'
                           ORDER BY period_end DESC LIMIT 1"""), "tol": 0.01},
        {"q": "Segundo o INE, qual é a renda mediana por m² dos novos contratos na freguesia de Odivelas?",
         "expect": one("""SELECT value FROM ine_values WHERE indicator = 'rent_ppm' AND council = 'Odivelas'
                           AND parish = 'Odivelas' ORDER BY period_end DESC LIMIT 1"""), "tol": 0.01},
        {"q": "Quantos T3 para arrendar até 1300 € por mês há em Loures?",
         "expect": one("""SELECT count(*) FROM mv_deals WHERE transaction_type = 'rent' AND council = 'Loures'
                           AND typology = 'T3' AND price_eur <= 1300"""), "tol": 0},
        {"q": "Qual é o preço de venda mediano por m² dos apartamentos em Benfica?", "expect": median_ppm("sale", "Lisboa", "Benfica"), "tol": 0.02},
        {"q": "Quantos imóveis temos guardados na shortlist?",
         "expect": one("SELECT count(*) FROM shortlist WHERE kind = 'listing' AND removed_at IS NULL"), "tol": 0},
        {"q": f"Quantos minutos de transportes demora o anúncio {sample} até ao trabalho da pessoa a?", "expect": sample_commute, "tol": 0},
        {"q": f"Quanto está o anúncio {sample} abaixo ou acima da mediana dos vizinhos, em percentagem?", "expect": abs(float(sample_disc)), "tol": 0.05},
        {"q": "Quanto custa ao todo, a 5 anos, uma carrinha híbrida de 20 000 € com 4 anos?", "expect": hybrid, "tol": 0.02},
        {"q": "Qual é o T2 mais barato para arrendar em Odivelas (concelho)?",
         "expect": one("""SELECT min(price_eur) FROM mv_deals WHERE transaction_type = 'rent' AND council = 'Odivelas'
                           AND typology = 'T2'"""), "tol": 0},
        {"q": "Quantos dias fica em mediana um anúncio de arrendamento no mercado na Estrela?",
         "expect": zones.get("Estrela", {}).get("median_days"), "tol": 0.05},
        {"q": "Quantos anúncios novos de arrendamento apareceram nos últimos 7 dias em Lisboa?",
         "expect": one("""SELECT count(*) FROM mv_deals WHERE transaction_type = 'rent' AND council = 'Lisboa'
                           AND first_seen_at > now() - interval '7 days'"""), "tol": 0.03},
        {"q": "Qual foi a variação a 12 meses das rendas em Alvalade?", "expect": abs(zones["Alvalade"]["change_12m"] or 0), "tol": 0.05},
        {"q": f"Por quanto foi efetivamente arrendado o anúncio {sample}?", "refusal": True},
        {"q": "Quantos carros Toyota Corolla estão anunciados nos modelos que seguimos?",
         "expect": one("""SELECT count(*) FROM v_car_current c JOIN car_models m ON m.id = c.model_id
                           WHERE m.label = 'Toyota Corolla' AND c.is_active"""), "tol": 0},
    ]
    return [c for c in out if c.get("refusal") or c.get("expect") is not None]


_NUM = re.compile(r"(?<![\w.])-?\d{1,3}(?:[ .  ]\d{3})+(?:,\d+)?|-?\d+(?:[.,]\d+)?")


def numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.findall(text or ""):
        s = m.replace(" ", " ").replace(" ", " ")
        if re.search(r"\d[ .]\d{3}(?!\d)", s) and "," not in s.split(" ")[-1][:1]:
            s = s.replace(" ", "").replace(".", "") if s.count(".") != 1 or re.search(r"\.\d{3}$", s) else s
        s = s.replace(" ", "").replace(",", ".")
        try:
            out.append(float(s))
        except ValueError:
            pass
    return out


def check(case: dict, answer: str) -> bool:
    if case.get("refusal"):
        a = answer.lower()
        return any(w in a for w in ("não sei", "não temos", "não há", "não tenho", "não é possível", "pedido",
                                    "não existe", "não conseguimos", "não dispon"))
    exp = float(case["expect"])
    tol = max(abs(exp) * case["tol"], 0.051 if exp < 100 else 1)
    ok = any(abs(n - exp) <= tol for n in numbers(answer))
    if ok and case.get("also"):
        ok = case["also"].lower() in answer.lower()
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="comma-separated case numbers, 1-based")
    ap.add_argument("--out")
    args = ap.parse_args()
    if not agent.configured():
        print("LLM_BASE_URL, LLM_API_KEY and LLM_MODEL are needed")
        return 2
    with connect() as conn:
        user = conn.execute("SELECT * FROM app_users ORDER BY slot LIMIT 1").fetchone()
    if not user:
        print("no app_users row: open the app once first")
        return 2
    all_cases = cases()
    pick = {int(x) for x in args.only.split(",")} if args.only else None
    results, cost = [], 0.0
    for i, case in enumerate(all_cases, 1):
        if pick and i not in pick:
            continue
        t = time.time()
        r = agent.ask(case["q"], None, user)
        ok = check(case, r["answer"])
        cost += (r.get("usage") or {}).get("cost_eur", 0)
        results.append({"n": i, "q": case["q"], "expect": case.get("expect"), "ok": ok,
                        "answer": r["answer"], "sources": r["sources"], "secs": round(time.time() - t, 1)})
        print(f"{'PASS' if ok else 'FAIL'} {i:2} {case['q'][:70]}  (esperado {case.get('expect', 'recusa')})", flush=True)
    passed = sum(r["ok"] for r in results)
    print(f"\n{passed}/{len(results)} passed, about {cost:.2f} € of tokens")
    if args.out:
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
