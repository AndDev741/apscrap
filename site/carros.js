/* Carros: the models you follow, their depreciation curve, five-year cost and
   adverts rated against similar cars (CA-1 to CA-4).

   Standvirtual's terms forbid repeated, systematic extraction, so nothing is
   crawled at night: a model's adverts are fetched when you follow it or press
   Atualizar (at most once a week per model), and a single advert when you paste
   its link. */
(function () {
  "use strict";
  const { h, api, nf, clear, add, s } = App;
  const CHIPS = [["", "Todos"], ["citadino", "Citadino"], ["suv", "SUV"], ["carrinha", "Carrinha"], ["eletrico", "Elétrico"]];
  const FUEL = { gasolina: "Gasolina", gasoleo: "Gasóleo", hibrido: "Híbrido", hibrido_plugin: "Híbrido plug-in", eletrico: "Elétrico", gpl: "GPL" };
  const BODY = { citadino: "Citadino", utilitario: "Utilitário", carrinha: "Carrinha", suv: "SUV", monovolume: "Monovolume", sedan: "Sedan", coupe: "Coupé", cabrio: "Cabrio", outro: "Outro" };
  const PART_COLORS = ["#C2622D", "#22385A", "#4F6A8F", "#8FA1BA", "#C5CEDB"];
  let body = "";
  let sel = null;

  function curve(dep, label) {
    const W = 520, H = 220, X = (t) => 40 + t * 47, Y = (r) => 20 + (1 - r) * 170;
    const svg = s("svg", { viewBox: "0 0 " + W + " " + H, role: "img", style: "width:100%;height:auto;display:block",
      "aria-label": label + ": mantém " + nf(dep.retention_3y * 100) + "% do valor aos 3 anos e " + nf(dep.retention_5y * 100) + "% aos 5 anos" });
    for (const [r, txt] of [[1, "100%"], [0.7, "70%"], [0.4, "40%"]]) {
      svg.appendChild(s("line", { x1: 40, y1: Y(r), x2: 510, y2: Y(r), stroke: "#ECE5D8" }));
      svg.appendChild(s("text", { x: 32, y: Y(r) + 4, "text-anchor": "end", "font-size": 11, fill: "#5C554A" }, txt));
    }
    svg.appendChild(s("line", { x1: 40, y1: 190, x2: 510, y2: 190, stroke: "#CFC5B3" }));
    [["0", 0], ["5 anos", 5], ["10 anos", 10]].forEach(([t, a]) => svg.appendChild(s("text", { x: X(a), y: 210, "text-anchor": a === 0 ? "start" : a === 10 ? "end" : "middle", "font-size": 11, fill: "#5C554A" }, t === "0" ? "novo" : t)));
    const path = (k) => { let d = ""; for (let t = 0; t <= 10; t += 0.5) d += (t ? " L" : "M") + X(t).toFixed(1) + " " + Y(Math.exp(-k * t)).toFixed(1); return d; };
    if (dep.segment_k) svg.appendChild(s("path", { d: path(dep.segment_k), fill: "none", stroke: "#8FA1BA", "stroke-width": 2, "stroke-dasharray": "5 5" }));
    // the model's own curve, relative to its value when new (p0)
    if (dep.curve && dep.curve.length && dep.p0) {
      const pts = dep.curve.filter((c) => c.age <= 10).map((c) => [X(c.age), Y(Math.min(c.price / dep.p0, 1.05))]);
      svg.appendChild(s("path", { d: pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" "), fill: "none", stroke: "#C2622D", "stroke-width": 3, "stroke-linejoin": "round" }));
    } else {
      svg.appendChild(s("path", { d: path(dep.k), fill: "none", stroke: "#C2622D", "stroke-width": 3 }));
    }
    for (const p of dep.points || []) {
      if (p.age > 10) continue;
      svg.appendChild(s("circle", { cx: X(p.age), cy: Y(Math.min(p.median_price / dep.p0, 1.05)), r: Math.min(3 + p.n / 4, 7), fill: "#C2622D", "fill-opacity": 0.35 }));
    }
    if (dep.sweet_spot_age !== null && dep.sweet_spot_age !== undefined && dep.sweet_spot_age <= 10) {
      const c = (dep.curve || []).find((x) => x.age === dep.sweet_spot_age);
      const r = c ? c.price / dep.p0 : Math.exp(-dep.k * dep.sweet_spot_age);
      svg.appendChild(s("circle", { cx: X(dep.sweet_spot_age), cy: Y(r), r: 7, fill: "#1E1B16" }));
    }
    return svg;
  }

  function tcoBlock(t) {
    if (!t) return null;
    return h("div", { style: { display: "flex", flexDirection: "column", gap: "10px" } },
      h("div", { class: "card-head", style: { alignItems: "baseline" } },
        h("h3", { style: { fontSize: "15px", fontWeight: 600 } }, "Custo total a 5 anos de um carro típico"),
        h("div", { class: "serif", style: { fontSize: "26px", fontWeight: 600 } }, App.eur(t.total), " ", h("span", { class: "small muted", style: { fontFamily: "var(--sans)", fontWeight: 400 } }, "· " + App.eur(t.per_month) + "/mês"))),
      h("div", { class: "stack", role: "img", "aria-label": "Custo total a 5 anos de " + App.eur(t.total) },
        t.parts.map((p, i) => h("div", { style: { width: Math.max(p.eur / t.total * 100, p.eur > 0 ? 1 : 0) + "%", background: PART_COLORS[i] } }))),
      h("div", { class: "parts" }, t.parts.map((p, i) => h("div", null,
        h("span", { class: "muted", style: { display: "flex", alignItems: "center", gap: "6px" } }, h("i", { style: { background: PART_COLORS[i] } }), p.nome),
        h("b", null, App.eur(p.eur))))),
      h("p", { class: "xsmall muted", style: { margin: 0 } }, nf(t.km_per_year) + " km por ano, IUC " + App.eur(t.iuc_year) + "/ano." +
        (t.estimates.length ? " Estimativas: " + t.estimates.join(", ") + ". Troquem pelos vossos valores nas Definições." : "")));
  }

  function followBox(onDone) {
    const box = h("div", { class: "card tight" }, h("h2", { style: { fontSize: "20px" } }, "Seguir um modelo"),
      h("p", { class: "sub", style: { margin: 0 } }, "Buscamos os anúncios desse modelo agora (até 5 páginas) e depois só quando carregarem em Atualizar, no máximo uma vez por semana. Os termos do Standvirtual não permitem recolha sistemática."));
    const makeSel = h("select", { "aria-label": "Marca" }, h("option", { value: "" }, "A carregar marcas…"));
    const modelSel = h("select", { "aria-label": "Modelo", disabled: true }, h("option", { value: "" }, "Modelo"));
    const go = h("button", { type: "button", class: "btn dark", disabled: true }, "Seguir");
    add(box, h("div", { class: "row wrap" }, makeSel, modelSel), go);
    let catalog = [];
    api("cars/catalog").then((c) => {
      catalog = c;
      clear(makeSel).appendChild(h("option", { value: "" }, "Marca"));
      c.forEach((m) => makeSel.appendChild(h("option", { value: m.make_slug }, m.make + (m.n ? " (" + nf(m.n) + ")" : ""))));
    }).catch(() => { clear(makeSel).appendChild(h("option", { value: "" }, "Catálogo indisponível")); });
    makeSel.addEventListener("change", () => {
      const mk = catalog.find((m) => m.make_slug === makeSel.value);
      clear(modelSel).appendChild(h("option", { value: "" }, "Modelo"));
      (mk ? mk.models : []).forEach((m) => modelSel.appendChild(h("option", { value: m.model_slug }, m.model + (m.n ? " (" + nf(m.n) + ")" : ""))));
      modelSel.disabled = !mk;
      go.disabled = true;
    });
    modelSel.addEventListener("change", () => { go.disabled = !modelSel.value; });
    go.addEventListener("click", async () => {
      go.disabled = true;
      go.textContent = "A buscar anúncios…";
      try {
        const m = await api("cars/follow", { body: { make_slug: makeSel.value, model_slug: modelSel.value } });
        sel = m.id;
        App.toast("A buscar os anúncios de " + m.label + ". Demora uns segundos por página.");
        setTimeout(onDone, 9000);
      } catch (e) { App.toast(e.message); go.disabled = false; go.textContent = "Seguir"; }
    });
    const url = h("input", { type: "url", placeholder: "https://www.standvirtual.com/carros/anuncio/…", "aria-label": "Link de um anúncio" });
    const addBtn = h("button", { type: "button", class: "btn", onclick: async () => {
      if (!url.value) return;
      addBtn.disabled = true;
      try { const r = await api("cars/add", { body: { url: url.value } }); App.go("#/carro/" + r.id); }
      catch (e) { App.toast(e.message); addBtn.disabled = false; }
    } }, "Guardar anúncio");
    add(box, h("label", { class: "field" }, "Ou colem um anúncio que encontraram", h("div", { class: "row" }, url, h("div", { style: { flex: "0 0 auto" } }, addBtn))));
    return box;
  }

  App.pages.carros = {
    title: "Carros",
    async render() {
      const ov = await api("cars/overview?" + App.qs({ body }));
      const models = ov.models;
      const root = h("frag");
      add(root, h("div", { class: "chips", role: "group", "aria-label": "Tipo de carro" }, CHIPS.map(([v, l]) =>
        h("button", { type: "button", class: "chip", "aria-pressed": body === v ? "true" : "false", onclick: () => { body = v; App.render(); } }, l))));
      const k = ov.kpi;
      add(root, h("section", { class: "kpis", "aria-label": "Resumo" },
        h("div", { class: "kpi" }, h("div", { class: "label" }, "Preço médio"), h("div", { class: "value" }, App.eur(k.avg_price)), h("div", { class: "note" }, body ? "segmento " + (CHIPS.find((c) => c[0] === body) || [])[1] : "modelos que seguem")),
        h("div", { class: "kpi" }, h("div", { class: "label" }, "Anúncios"), h("div", { class: "value" }, nf(k.listings)), h("div", { class: "note" }, "nos modelos que seguem")),
        h("div", { class: "kpi" }, h("div", { class: "label" }, "Idade média"), h("div", { class: "value" }, k.avg_age !== null ? nf(k.avg_age, 1) + " anos" : "–"), h("div", { class: "note" }, k.avg_km ? nf(k.avg_km) + " km em média" : "")),
        h("div", { class: "kpi dark" }, h("div", { class: "label" }, "Ponto doce de compra"), h("div", { class: "value" }, k.sweet_spot_age !== null ? nf(k.sweet_spot_age) + " anos" : "–"),
          h("div", { class: "note" }, k.best_model ? "quando a curva abranda · " + k.best_model + " desvaloriza menos" : "sigam modelos para comparar"))));

      if (!models.length) {
        add(root, h("div", { class: "split" }, h("div", { class: "main empty" }, h("b", null, body ? "Nenhum modelo seguido neste segmento" : "Ainda não seguem nenhum modelo"),
          h("span", { class: "muted" }, "Para uma família com uma criança pequena, comecem por carrinhas e SUV compactos com Isofix: por exemplo Toyota Corolla Touring Sports, Dacia Jogger, Škoda Octavia Break ou Kia Ceed SW.")),
          h("div", { class: "side" }, followBox(() => App.render()))));
        return root;
      }
      if (!sel || !models.find((m) => m.id === sel)) sel = models[0].id;
      const m = models.find((x) => x.id === sel);
      const maxP = Math.max(...models.map((x) => x.med_price || 0)) || 1;
      const bars = h("div", { class: "bars" }, models.slice().sort((a, b) => (b.med_price || 0) - (a.med_price || 0)).map((x) =>
        h("button", { type: "button", class: "bar", "aria-pressed": x.id === sel ? "true" : "false", onclick: () => { sel = x.id; App.render(); } },
          h("span", { class: "l" }, h("span", { style: { fontWeight: 500 } }, x.label + " · n=" + nf(x.n)), h("span", { style: { fontWeight: 600 } }, App.eur(x.med_price))),
          h("span", { class: "track" }, h("span", { class: "fill", style: { width: Math.round((x.med_price || 0) / maxP * 100) + "%" } })))));
      const dep = m.depreciation;
      const fetched = m.last_fetched_at ? new Date(m.last_fetched_at) : null;
      const nextOk = fetched ? new Date(fetched.getTime() + 7 * 86400000) : null;
      const refresh = h("button", { type: "button", class: "btn", disabled: nextOk && nextOk > new Date(), title: nextOk && nextOk > new Date() ? "Pode voltar a atualizar a " + App.dateShort(nextOk) : null,
        onclick: async () => { await api("cars/model/" + m.id + "/refresh", { method: "POST" }); App.toast("A atualizar " + m.label + "…"); setTimeout(App.render, 9000); } },
        App.icon("refresh", 16), fetched ? "Atualizado " + App.ago(m.last_fetched_at) : "Atualizar");
      const detail = h("div", { class: "card", style: { flex: "3 1 520px" } },
        h("div", { class: "card-head", style: { alignItems: "flex-start" } },
          h("div", null, h("div", { class: "xsmall muted", style: { textTransform: "uppercase", letterSpacing: ".08em" } }, [BODY[m.body] || m.body, FUEL[m.fuel] || m.fuel].filter(Boolean).join(" · ")),
            h("h2", { style: { fontSize: "32px", lineHeight: 1.1, marginTop: "4px" } }, m.label)),
          h("div", { class: "row", style: { flex: "0 0 auto" } }, refresh,
            h("button", { type: "button", class: "btn", onclick: async () => { if (confirm("Deixar de seguir " + m.label + "?")) { await api("cars/model/" + m.id + "/unfollow", { method: "POST" }); sel = null; App.render(); } } }, "Deixar de seguir"))),
        dep.enough ? h("figure", { style: { margin: 0, display: "flex", flexDirection: "column", gap: "8px" } },
          h("figcaption", { style: { fontSize: "14px", fontWeight: 500 } }, "Curva de depreciação · valor que mantém face a novo"),
          curve(dep, m.label),
          h("div", { class: "row wrap xsmall muted", style: { gap: "20px" } },
            h("span", { style: { display: "flex", alignItems: "center", gap: "6px", flex: "0 0 auto" } }, h("span", { style: { width: "18px", height: "3px", background: "#C2622D", display: "inline-block" } }), m.label),
            dep.segment_k ? h("span", { style: { display: "flex", alignItems: "center", gap: "6px", flex: "0 0 auto" } }, h("span", { style: { width: "18px", borderTop: "2px dashed #8FA1BA", display: "inline-block" } }), "Média do segmento (" + dep.segment_models + " modelos)") : null,
            h("span", { style: { flex: "0 0 auto" } }, "Mantém " + nf(dep.retention_3y * 100) + "% aos 3 anos e " + nf(dep.retention_5y * 100) + "% aos 5"),
            dep.sweet_spot_age !== null ? h("span", { style: { flex: "0 0 auto" } }, "● ponto doce: " + dep.sweet_spot_age + " anos") : null),
          h("p", { class: "xsmall muted", style: { margin: 0 } }, "Preços de hoje de carros de idades diferentes (n=" + dep.n + "), não um carro seguido ao longo dos anos. Cada ponto é a mediana de uma idade."))
          : h("p", { class: "reading" }, "Ainda há poucos anúncios para desenhar a curva (" + dep.n + " de " + dep.min_n + " necessários)."),
        tcoBlock(m.tco));
      add(root, h("section", { class: "split" },
        h("div", { class: "card", style: { flex: "2 1 420px" } },
          h("div", null, h("h2", null, "Preço mediano por modelo"), h("p", { class: "sub" }, "Usados, todo o país · toca num modelo para o analisar")), bars,
          followBox(() => App.render())),
        detail));

      const deals = await api("cars?" + App.qs({ model_id: m.id, sort: "discount", limit: 8 }));
      add(root, h("section", { class: "card" },
        h("div", { class: "card-head" }, h("div", null, h("h2", null, "Anúncios comparados"), h("p", { class: "sub" }, "Preço face a carros semelhantes (mesmo modelo e combustível, ano ±1, km ±20 000)")),
          h("a", { href: "#/anuncios?" + App.qs({ cat: "carros", model_id: m.id }) }, "Ver todos")),
        h("div", { class: "table-wrap" }, h("table", { class: "data" },
          h("thead", null, h("tr", null, ["Anúncio", "Ano", "Km", "Preço", "Semelhantes", "Avaliação"].map((c, i) => h("th", { scope: "col", class: i >= 3 && i <= 4 ? "num" : null }, c)))),
          h("tbody", null, deals.items.map((c) => h("tr", null,
            h("td", { style: { fontWeight: 500 } }, h("a", { href: "#/carro/" + c.id, style: { color: "inherit" } }, c.title)),
            h("td", null, c.year), h("td", null, c.km !== null ? nf(c.km) : "–"),
            h("td", { class: "num", style: { fontWeight: 600 } }, App.eur(c.price_eur)),
            h("td", { class: "num muted" }, c.peer_med_price ? App.eur(c.peer_med_price) + " (n=" + c.peer_n + ")" : "–"),
            h("td", null, c.rating === "bom" ? h("span", { class: "pill good" }, "Bom negócio · " + nf(c.discount_pct) + "% abaixo")
              : c.rating === "acima" ? h("span", { class: "pill bad" }, "Acima · +" + nf(-c.discount_pct) + "%")
              : c.rating === "justo" ? h("span", { class: "pill neutral" }, "Preço justo") : h("span", { class: "muted small" }, "sem semelhantes")))))))));
      return root;
    },
  };
})();
