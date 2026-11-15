/* Imóveis: the dashboard. Summary tiles, the zone map (a cartogram of the
   parishes being followed), the selected zone, adverts below the median with
   both votes, and the market diary. */
(function () {
  "use strict";
  const { h, api, nf, clear, add } = App;
  const RAMP = ["#E6E9EE", "#C5CEDB", "#8FA1BA", "#4F6A8F", "#22385A"];
  let selected = null;
  let typology = "";

  function modeToggle(onChange) {
    const mode = App.state.mode;
    return h("div", { class: "seg", role: "group", "aria-label": "Mercado" },
      [["sale", "Venda"], ["rent", "Arrendamento"]].map(([m, label]) =>
        h("button", { type: "button", "aria-pressed": mode === m ? "true" : "false", onclick: () => { App.setMode(m); onChange(); } }, label)));
  }

  function kpis(o, mode) {
    return h("section", { class: "kpis", "aria-label": "Resumo" },
      h("div", { class: "kpi" }, h("div", { class: "label" }, mode === "sale" ? "Preço mediano de venda" : "Renda mediana"),
        h("div", { class: "value" }, App.ppm(o.median_ppm, mode)),
        h("div", { class: "note" }, "apartamentos em " + o.councils.join(", "))),
      h("div", { class: "kpi" }, h("div", { class: "label" }, "Anúncios ativos"), h("div", { class: "value" }, nf(o.active)),
        h("div", { class: "note up" }, "▲ " + nf(o.new_7d) + " novos esta semana")),
      h("div", { class: "kpi" }, h("div", { class: "label" }, "Baixas de preço (7 dias)"), h("div", { class: "value" }, nf(o.drops_7d)),
        h("div", { class: "note" }, o.drops_saved ? nf(o.drops_saved) + " em anúncios da vossa shortlist" : "nenhuma na vossa shortlist")),
      h("a", { class: "kpi dark", href: "#/shortlist", style: { textDecoration: "none" } }, h("div", { class: "label" }, "Shortlist a dois"),
        h("div", { class: "value" }, nf(o.agree), " ", h("small", null, "de acordo")),
        h("div", { class: "note" }, o.saved ? "em " + nf(o.saved) + " imóveis guardados" : "guardem anúncios para votar")));
  }

  function buckets(zones) {
    const vals = zones.map((z) => z.median_ppm).filter((v) => v !== null).sort((a, b) => a - b);
    const q = (p) => vals[Math.floor(p * (vals.length - 1))];
    return { th: [q(0.2), q(0.4), q(0.6), q(0.8)], min: vals[0], max: vals[vals.length - 1] };
  }

  function zoneMap(data, mode, onPick) {
    const z = data.zones.filter((x) => x.col);
    const b = buckets(z);
    const label = (v) => (v === null || v === undefined ? "–" : mode === "sale" ? nf(v / 1000, 1) + "k" : nf(v, 1));
    const bucket = (v) => { let i = 0; while (i < 4 && v > b.th[i]) i++; return i; };
    const grid = h("div", { class: "carto" });
    for (const t of z) {
      const thin = t.median_ppm === null;
      const i = thin ? 0 : bucket(t.median_ppm);
      const ch = t.change_12m;
      grid.appendChild(h("button", {
        type: "button", class: "tile" + (thin ? " thin" : ""), "aria-pressed": selected === t.parish ? "true" : "false",
        title: t.parish + (thin ? " (n=" + t.n + ", poucos anúncios)" : ""),
        "aria-label": t.parish + ", " + (thin ? "só " + t.n + " anúncios" : App.ppm(t.median_ppm, mode)) + (ch !== null ? ", " + (ch >= 0 ? "subida" : "descida") + " de " + nf(Math.abs(ch), 1) + "% em 12 meses" : ""),
        style: { gridColumn: t.col, gridRow: t.row, background: thin ? null : RAMP[i], color: !thin && i >= 3 ? "#FFFFFF" : "#1E1B16" },
        onclick: () => onPick(t.parish),
      }, h("span", { class: "nm" }, t.short), h("span", { class: "vl" }, h("b", null, thin ? "n=" + t.n : label(t.median_ppm)), h("span", null, ch === null ? "" : ch >= 0 ? "▲" : "▼"))));
    }
    const typChips = h("div", { class: "chips", role: "group", "aria-label": "Tipologia" },
      [["", "Todas"], ["T1", "T1"], ["T2", "T2"], ["T3", "T3"], ["T4+", "T4+"]].map(([v, l]) =>
        h("button", { type: "button", class: "chip sq", "aria-pressed": typology === v ? "true" : "false", onclick: () => { typology = v; App.render(); } }, l)));
    return h("div", { class: "card main" },
      h("div", { class: "card-head" },
        h("div", null, h("h2", null, "Mapa por zona"), h("p", { class: "sub" }, (mode === "sale" ? "Venda · milhares de €/m²" : "Arrendamento · €/m² por mês") + " · toca numa zona para ver o detalhe")),
        h("div", { class: "legend", "aria-label": "Legenda" }, h("span", null, label(b.min)), h("div", { class: "ramp" }, RAMP.map((c) => h("i", { style: { background: c } }))), h("span", null, label(b.max)))),
      typChips, grid,
      h("p", { class: "xsmall muted", style: { margin: 0 } }, "Cada mosaico é uma freguesia em posição aproximada, norte em cima. Seta = tendência a 12 meses (INE enquanto o nosso histórico for curto). Mosaicos às riscas têm menos de " + data.min_n + " anúncios."));
  }

  function zonePanel(t, mode) {
    if (!t) return h("aside", { class: "card side" }, h("p", { class: "muted" }, "Escolhe uma zona no mapa."));
    const ch = t.change_12m;
    const src = t.spark.source === "ine" ? "INE, trimestral" : "apScrap, semanal";
    const pts = t.spark.points || [];
    return h("aside", { class: "card side", "aria-label": "Detalhe da zona" },
      h("div", { class: "card-head" },
        h("div", null, h("div", { class: "xsmall muted", style: { textTransform: "uppercase", letterSpacing: ".08em" } }, "Zona selecionada · " + t.council),
          h("h2", { style: { fontSize: "34px", lineHeight: 1.1, marginTop: "4px" } }, t.parish))),
      h("div", { class: "zone-main" },
        h("div", { class: "big" }, t.median_ppm === null ? "–" : mode === "sale" ? nf(t.median_ppm) : nf(t.median_ppm, 1)),
        h("div", { class: "muted" }, mode === "sale" ? "€/m² · venda" : "€/m² por mês · renda"),
        ch !== null ? h("span", { class: "pill " + (ch >= 0 ? "good" : "bad") }, (ch >= 0 ? "▲ +" : "▼ −") + nf(Math.abs(ch), 1) + "% em 12 meses" + (t.change_12m_source === "ine" ? " (INE)" : "")) : null),
      h("figure", { style: { margin: 0, display: "flex", flexDirection: "column", gap: "6px" } },
        App.sparkline(pts, "Evolução em " + t.parish),
        h("figcaption", { class: "xsmall muted", style: { display: "flex", justifyContent: "space-between" } },
          h("span", null, pts.length ? App.dateShort(pts[0][0]) : ""), h("span", null, src), h("span", null, pts.length ? App.dateShort(pts[pts.length - 1][0]) : ""))),
      h("div", { class: "stats" },
        h("div", { class: "stat" }, h("div", { class: "k" }, mode === "sale" ? "Renda mediana" : "Preço de venda"), h("div", { class: "v" }, App.ppm(t.other_median_ppm, mode === "sale" ? "rent" : "sale"))),
        h("div", { class: "stat" }, h("div", { class: "k" }, "Rendimento bruto"), h("div", { class: "v" }, t.gross_yield ? nf(t.gross_yield, 1) + "%" : "–")),
        h("div", { class: "stat" }, h("div", { class: "k" }, "Dias no mercado (mediana)"), h("div", { class: "v" }, t.median_days !== null ? nf(t.median_days) + " dias" : "–")),
        h("div", { class: "stat" }, h("div", { class: "k" }, "Anúncios ativos"), h("div", { class: "v" }, nf(t.n)))),
      t.ine_latest ? h("p", { class: "xsmall muted", style: { margin: 0 } }, "INE (" + (mode === "sale" ? "vendas" : "novos contratos") + ", 12 meses até " + App.dateShort(t.ine_latest.period_end) + "): " + App.ppm(t.ine_latest.value, mode)) : null,
      h("p", { class: "reading" }, t.reading),
      h("a", { class: "btn dark big", href: "#/anuncios?" + App.qs({ mode, council: t.council, parish: t.parish }) }, "Ver anúncios em " + App.short(t.parish)));
  }

  function deals(items, mode) {
    return h("div", { class: "main", id: "negocios", style: { display: "flex", flexDirection: "column", gap: "14px" } },
      h("div", { class: "card-head" },
        h("div", null, h("h2", { class: "serif", style: { fontSize: "24px" } }, "Abaixo da mediana"),
          h("p", { class: "sub muted small" }, "Melhores negócios novos desta semana dentro do vosso orçamento · o teu voto e o de " + App.userName(App.other()))),
        h("a", { href: "#/anuncios?" + App.qs({ mode, below: 1, sort: "discount" }) }, "Ver todos")),
      items.length ? h("div", { class: "grid-cards" }, items.map((r) => App.listingCard(r, { votes: true, compact: true })))
        : h("div", { class: "empty" }, h("span", { class: "muted" }, "Nada abaixo da mediana esta semana dentro dos critérios.")));
  }

  function diary(o, diaryRows) {
    const last = diaryRows && diaryRows[0];
    return h("aside", { class: "card side", "aria-label": "Diário de mercado" },
      h("h2", null, "Diário de mercado"),
      h("p", { class: "sub", style: { margin: 0 } }, "O que mudou nas zonas que seguem"),
      last && last.prose ? h("p", { class: "reading" }, last.prose) : null,
      o.events.length ? h("ul", { class: "feed" }, o.events.map((e) =>
        h("li", null, h("div", { class: "ic " + e.kind, "aria-hidden": "true" }, e.kind === "up" ? "▲" : e.kind === "down" ? "▼" : "+"),
          h("div", null, e.listing_id ? h("a", { href: "#/anuncio/" + e.listing_id, style: { fontWeight: 500, color: "inherit" } }, e.texto) : h("div", { style: { fontWeight: 500 } }, e.texto),
            h("div", { class: "xsmall muted" }, App.ago(e.when))))))
        : h("p", { class: "muted small" }, "Ainda sem movimentos a assinalar."),
      h("a", { class: "btn", href: "#/definicoes" }, "Gerir alertas"));
  }

  App.pages.imoveis = {
    title: "Imóveis",
    async render() {
      const mode = App.state.mode || "rent";
      App.state.headerExtra = modeToggle(() => App.render());
      const pc = App.state.me.settings.property_criteria;
      const [o, zones, dl, diaryRows] = await Promise.all([
        api("overview?mode=" + mode),
        api("zones?" + App.qs({ mode, typology })),
        api("listings?" + App.qs({ mode, below: 1, sort: "discount", new_days: 7, limit: 3,
          price_max: mode === "rent" ? pc.max_price : null, typologies: mode === "rent" ? pc.typologies : null })),
        api("diary"),
      ]);
      const withData = zones.zones.filter((z) => z.median_ppm !== null);
      if (!selected || !zones.zones.find((z) => z.parish === selected)) {
        const best = withData.sort((a, b) => b.n - a.n)[0];
        selected = best ? best.parish : null;
      }
      const root = h("frag");
      const mapWrap = h("section", { class: "split" });
      const draw = () => {
        clear(mapWrap);
        add(mapWrap, [zoneMap(zones, mode, (p) => { selected = p; draw(); }), zonePanel(zones.zones.find((z) => z.parish === selected), mode)]);
      };
      draw();
      add(root, [kpis(o, mode), mapWrap, h("section", { class: "split" }, deals(dl.items, mode), diary(o, diaryRows))]);
      return root;
    },
  };
})();
