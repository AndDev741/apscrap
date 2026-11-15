/* Shortlist (DU-1) and side-by-side comparison (DU-2). Saved adverts are
   grouped by what the two of you think: both like it, you disagree, one of you
   has not voted yet. Pick two to four and compare them row by row. */
(function () {
  "use strict";
  const { h, api, nf, clear, add } = App;
  let kind = "listing";
  const picked = new Set();

  function group(votes) {
    const a = (votes || {}).a, b = (votes || {}).b;
    if (a === "gosto" && b === "gosto") return "agree";
    if (a === "nao" && b === "nao") return "no";
    if (a && b && a !== b) return "disagree";
    return "pending";
  }
  const GROUPS = [["agree", "Os dois gostam"], ["disagree", "Não estão de acordo"], ["pending", "Falta votar"], ["no", "Os dois disseram não"]];

  function pickBox(id, label, onChange) {
    return h("label", { class: "check", style: { minHeight: "36px" } },
      h("input", { type: "checkbox", checked: picked.has(id), onchange: (e) => { e.target.checked ? picked.add(id) : picked.delete(id); onChange(); } }), label);
  }

  App.pages.shortlist = {
    title: "Shortlist",
    async render() {
      const data = await api("shortlist");
      const items = kind === "listing" ? data.listings : data.cars;
      const root = h("frag");
      const compareBtn = h("a", { class: "btn dark big" });
      const syncCompare = () => {
        const n = picked.size;
        compareBtn.textContent = n >= 2 ? "Comparar " + n : "Escolham 2 a 4 para comparar";
        compareBtn.setAttribute("href", n >= 2 && n <= 4 ? "#/comparar?" + App.qs({ kind, ids: [...picked] }) : "#/shortlist");
        compareBtn.setAttribute("aria-disabled", n >= 2 && n <= 4 ? "false" : "true");
        compareBtn.style.opacity = n >= 2 && n <= 4 ? 1 : 0.5;
      };
      syncCompare();
      add(root, h("div", { class: "page-head" },
        h("div", null, h("h1", null, "Shortlist a dois"),
          h("p", { class: "muted", style: { margin: "6px 0 0" } }, "Guardem com o coração ou votem num anúncio. Os votos seguem o imóvel se for retirado e republicado.")),
        h("div", { class: "row", style: { flex: "0 0 auto", alignItems: "center" } },
          h("div", { class: "pills", role: "group", "aria-label": "Categoria" },
            [["listing", "Imóveis · " + data.listings.length], ["car", "Carros · " + data.cars.length]].map(([k, l]) =>
              h("button", { type: "button", "aria-pressed": kind === k ? "true" : "false", onclick: () => { kind = k; picked.clear(); App.render(); } }, l))),
          compareBtn)));
      if (!items.length) {
        add(root, h("div", { class: "empty" }, h("b", null, "Ainda nada guardado"),
          h("span", { class: "muted" }, kind === "listing" ? "Toquem no coração de um anúncio ou votem em Imóveis e Anúncios." : "Guardem carros no separador Carros ou colem um anúncio do Standvirtual."),
          h("a", { class: "btn dark", href: kind === "listing" ? "#/anuncios" : "#/carros" }, kind === "listing" ? "Ver anúncios" : "Ir para Carros")));
        return root;
      }
      for (const [g, label] of GROUPS) {
        const list = items.filter((r) => group(r.votes) === g);
        if (!list.length) continue;
        add(root, h("section", { style: { display: "flex", flexDirection: "column", gap: "12px" } },
          h("h2", { class: "serif", style: { fontSize: "24px" } }, label + " · " + list.length),
          h("div", { class: "grid-cards" }, list.map((r) => {
            const card = kind === "listing" ? App.listingCard(r, { votes: true }) : App.carCard(r);
            const body = card.querySelector(".body");
            if (kind === "car") body.insertBefore(App.votes("car", r.id, r.votes, () => App.render()), body.querySelector(".foot"));
            if (kind === "listing" && r.is_active === false) body.insertBefore(h("span", { class: "pill bad" }, "Já não está anunciado"), body.firstChild);
            if (r.n_notes) body.insertBefore(h("a", { class: "small", href: (kind === "listing" ? "#/anuncio/" : "#/carro/") + r.id }, r.n_notes + (r.n_notes === 1 ? " nota de visita" : " notas de visita")), body.querySelector(".foot"));
            body.appendChild(pickBox(r.id, "Comparar", syncCompare));
            return card;
          }))));
      }
      return root;
    },
  };

  /* -- comparison --------------------------------------------------------- */
  function best(values, dir) {
    const nums = values.map((v) => (typeof v === "number" && !isNaN(v) ? v : null));
    const valid = nums.filter((v) => v !== null);
    if (valid.length < 2) return -1;
    const target = dir === "min" ? Math.min(...valid) : Math.max(...valid);
    return nums.filter((v) => v === target).length === 1 ? nums.indexOf(target) : -1;
  }

  App.pages.comparar = {
    title: "Comparar", tab: "shortlist",
    async render({ params }) {
      const k = params.get("kind") || "listing";
      const ids = (params.get("ids") || "").split(",").filter(Boolean);
      const data = await api("shortlist");
      let items = (k === "listing" ? data.listings : data.cars).filter((r) => ids.includes(String(r.id)) || ids.includes(String(r.saved_id)));
      if (k === "listing") {
        const missing = ids.filter((id) => !items.find((r) => String(r.id) === id));
        for (const id of missing) items.push(await api("listing/" + id));
      }
      const V = { gosto: "Gosto", talvez: "Talvez", nao: "Não" };
      const rows = k === "listing" ? [
        ["Preço", (r) => r.price_eur, "min", (r) => r.transaction_type === "sale" ? App.eur(r.price_eur) : App.eur(r.price_eur) + "/mês"],
        ["Área", (r) => r.area_sqm, "max", (r) => (r.area_sqm ? nf(r.area_sqm) + " m²" : "–")],
        ["€/m²", (r) => r.price_per_sqm, "min", (r) => App.ppm(r.price_per_sqm, r.transaction_type)],
        ["Face à zona", (r) => r.discount_pct, "max", (r) => (r.discount_pct === null || r.discount_pct === undefined ? "–" : (r.discount_pct >= 0 ? nf(r.discount_pct) + "% abaixo" : nf(-r.discount_pct) + "% acima") + " (n=" + r.peer_n + ")")],
        ["Baixas de preço", (r) => r.n_drops, "max", (r) => nf(r.n_drops || 0)],
        ["Dias no mercado", (r) => r.chain_days_on_market ?? r.days_on_market, "max", (r) => App.days(r.chain_days_on_market ?? r.days_on_market)],
        [App.userName("a") + " (transportes)", (r) => (r.commute || {}).a_transit, "min", (r) => ((r.commute || {}).a_transit !== undefined ? r.commute.a_transit + " min" : "–")],
        [App.userName("b") + " (transportes)", (r) => (r.commute || {}).b_transit, "min", (r) => ((r.commute || {}).b_transit !== undefined ? r.commute.b_transit + " min" : "–")],
        ["Extras", () => null, null, (r) => (r.extras || []).join(", ") || "–"],
        ["Andar · certificado", () => null, null, (r) => [r.floor, r.energy_cert].filter(Boolean).join(" · ") || "–"],
        ["Votos", () => null, null, (r) => App.initial("a") + ": " + (V[(r.votes || {}).a] || "–") + " · " + App.initial("b") + ": " + (V[(r.votes || {}).b] || "–")],
        ["Notas de visita", (r) => r.n_notes, null, (r) => nf(r.n_notes || 0)],
      ] : [
        ["Preço", (r) => r.price_eur, "min", (r) => App.eur(r.price_eur)],
        ["Ano", (r) => r.year, "max", (r) => r.year || "–"],
        ["Km", (r) => r.km, "min", (r) => (r.km !== null ? nf(r.km) : "–")],
        ["Face a semelhantes", (r) => r.discount_pct, "max", (r) => (r.discount_pct === null || r.discount_pct === undefined ? "–" : (r.discount_pct >= 0 ? nf(r.discount_pct) + "% abaixo" : nf(-r.discount_pct) + "% acima"))],
        ["Custo a 5 anos", (r) => r.tco && r.tco.total, "min", (r) => (r.tco ? App.eur(r.tco.total) : "–")],
        ["Custo por mês", (r) => r.tco && r.tco.per_month, "min", (r) => (r.tco ? App.eur(r.tco.per_month) : "–")],
        ["IUC por ano", (r) => r.tco && r.tco.iuc_year, "min", (r) => (r.tco ? App.eur(r.tco.iuc_year) : "–")],
        ["Combustível · caixa", () => null, null, (r) => [r.fuel, r.gearbox].filter(Boolean).join(" · ")],
        ["Votos", () => null, null, (r) => App.initial("a") + ": " + (V[(r.votes || {}).a] || "–") + " · " + App.initial("b") + ": " + (V[(r.votes || {}).b] || "–")],
      ];
      const title = (r) => (k === "listing" ? (r.typology ? r.typology + " · " : "") + App.short(r.parish || r.council) : r.title);
      const root = h("frag");
      add(root, h("div", { class: "page-head" }, h("div", null, h("a", { href: "#/shortlist", class: "small" }, "← Shortlist"), h("h1", { style: { marginTop: "6px" } }, "Lado a lado")),
        h("p", { class: "muted small", style: { margin: 0 } }, "A verde, o melhor valor de cada linha.")));
      add(root, h("div", { class: "card" }, h("div", { class: "table-wrap" }, h("table", { class: "data compare", style: { minWidth: 200 + items.length * 180 + "px" } },
        h("thead", null, h("tr", null, h("th", { scope: "col" }, ""), items.map((r) => h("th", { scope: "col" },
          h("a", { href: (k === "listing" ? "#/anuncio/" : "#/carro/") + r.id, style: { fontSize: "15px", fontWeight: 600, color: "var(--ink)" } }, title(r)))))),
        h("tbody", null,
          h("tr", null, h("th", { scope: "row" }, "Foto"), items.map((r) => h("td", null, (r.image_urls && r.image_urls[0]) || r.thumbnail_url ? h("img", { src: (r.image_urls && r.image_urls[0]) || r.thumbnail_url, alt: "", referrerpolicy: "no-referrer", style: { width: "100%", maxWidth: "220px", aspectRatio: "4/3", objectFit: "cover", borderRadius: "10px" } }) : "–"))),
          rows.map(([label, get, dir, fmt]) => {
            const vals = items.map(get);
            const b = dir ? best(vals, dir) : -1;
            return h("tr", null, h("th", { scope: "row" }, label), items.map((r, i) => h("td", { class: i === b ? "best" : null }, fmt(r))));
          }))))));
      return root;
    },
  };
})();
