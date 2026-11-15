/* Anúncios: every advert with filters, photos and links. Properties come from
   Imovirtual, RE/MAX, Casa Sapo, ERA and KW every night; cars only from the models you follow, fetched when
   you press the button (Standvirtual's terms). The filters live in the URL so
   a search can be shared between the two of you or saved as an alert. */
(function () {
  "use strict";
  const { h, api, nf, clear, add } = App;
  const PAGE = 24;
  const EXTRAS = ["Garagem", "Elevador", "Varanda", "Mobilado"];
  const SORTS_IM = [["recent", "Mais recentes"], ["discount", "Maior desconto"], ["price_asc", "Preço mais baixo"], ["price_desc", "Preço mais alto"], ["ppm_asc", "€/m² mais baixo"], ["commute", "Mais perto do trabalho"], ["drops", "Mais baixas de preço"]];
  const SORTS_CAR = [["discount", "Maior desconto"], ["price_asc", "Preço mais baixo"], ["price_desc", "Preço mais alto"], ["km_asc", "Menos km"], ["recent", "Mais recente"], ["new", "Anunciados há menos tempo"]];
  const BODY_LABEL = { citadino: "Citadino", utilitario: "Utilitário", carrinha: "Carrinha", suv: "SUV", monovolume: "Monovolume", sedan: "Sedan", coupe: "Coupé", cabrio: "Cabrio", outro: "Outro" };
  const CAR_TYPES = [["citadino", "Citadino"], ["suv", "SUV"], ["carrinha", "Carrinha"], ["eletrico", "Elétrico"]];
  const FUELS = [["", "Todos"], ["gasolina", "Gasolina"], ["gasoleo", "Gasóleo"], ["hibrido", "Híbrido"], ["hibrido_plugin", "Híbrido plug-in"], ["eletrico", "Elétrico"], ["gpl", "GPL"]];

  function readFilters(params) {
    const g = (k) => params.get(k) || "";
    const l = (k) => g(k).split(",").filter(Boolean);
    return {
      cat: g("cat") || "imoveis",
      mode: g("mode") || App.state.mode || "rent",
      council: g("council"), parish: g("parish"),
      typologies: l("typologies"), price_max: g("price_max"), area_min: g("area_min"),
      extras: l("extras"), below: g("below") === "1", drops: g("drops") === "1",
      midstay: g("midstay") === "1", sources: l("sources"),
      max_commute: g("max_commute"), commute_who: g("commute_who") || "both", commute_mode: g("commute_mode") || "transit",
      sort: g("sort") || "", view: g("view") || "grid",
      body: g("body"), fuel: g("fuel"), year_min: g("year_min"), km_max: g("km_max"), cprice_max: g("cprice_max"),
      automatic: g("automatic") === "1", good: g("good") === "1", model_id: g("model_id"),
    };
  }

  function propertyQuery(f, extra = {}) {
    return App.qs({ mode: f.mode, council: f.council, parish: f.parish, typologies: f.typologies, price_max: f.price_max, area_min: f.area_min,
      extras: f.extras, below: f.below, drops: f.drops, max_commute: f.max_commute, commute_who: f.max_commute ? f.commute_who : null,
      commute_mode: f.max_commute ? f.commute_mode : null, midstay: f.midstay, sources: f.sources, sort: f.sort || "recent", ...extra });
  }
  function carQuery(f, extra = {}) {
    return App.qs({ body: f.body, fuel: f.fuel, year_min: f.year_min, km_max: f.km_max, price_max: f.cprice_max, automatic: f.automatic, good: f.good,
      model_id: f.model_id, sort: f.sort || "discount", ...extra });
  }

  function update(f, patch) {
    Object.assign(f, patch);
    const keep = { ...f };
    if (keep.view === "grid") delete keep.view;
    App.setQuery(keep);
    App.render();
  }

  function propertyFilters(f, facets) {
    const zones = facets.zones || [];
    const byCouncil = {};
    for (const z of zones) (byCouncil[z.council] = byCouncil[z.council] || []).push(z);
    const zoneSel = h("select", { id: "f-zona", onchange: (e) => { const [c, p] = e.target.value.split("|"); update(f, { council: c || "", parish: p || "" }); } },
      h("option", { value: "" }, "Todas as zonas"),
      Object.entries(byCouncil).map(([c, list]) => h("optgroup", { label: c },
        h("option", { value: c + "|", selected: f.council === c && !f.parish }, c + " (todo o concelho)"),
        list.map((z) => h("option", { value: c + "|" + z.parish, selected: f.parish === z.parish }, App.short(z.parish) + " · " + z.n)))));
    const toggle = (key, v) => () => { const a = new Set(f[key]); a.has(v) ? a.delete(v) : a.add(v); update(f, { [key]: [...a] }); };
    let priceTimer;
    const num = (key, label, placeholder) => h("label", { class: "field" }, label,
      h("input", { type: "number", inputmode: "numeric", placeholder, value: f[key], onchange: (e) => update(f, { [key]: e.target.value }),
        oninput: (e) => { clearTimeout(priceTimer); priceTimer = setTimeout(() => update(f, { [key]: e.target.value }), 900); } }));
    return h("div", { style: { display: "flex", flexDirection: "column", gap: "20px" } },
      h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } }, h("div", { class: "small", style: { fontWeight: 600 } }, "Negócio"),
        h("div", { class: "seg fill", role: "group", "aria-label": "Negócio" },
          [["sale", "Comprar"], ["rent", "Arrendar"]].map(([m, l]) => h("button", { type: "button", "aria-pressed": f.mode === m ? "true" : "false",
            onclick: () => { App.setMode(m); update(f, { mode: m, price_max: "" }); } }, l)))),
      h("label", { class: "field", for: "f-zona" }, "Zona", zoneSel),
      h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } }, h("div", { class: "small", style: { fontWeight: 600 } }, "Tipologia"),
        h("div", { class: "chips", role: "group", "aria-label": "Tipologia" }, ["T0", "T1", "T2", "T3", "T4+"].map((t) =>
          h("button", { type: "button", class: "chip sq", "aria-pressed": f.typologies.includes(t) ? "true" : "false", onclick: toggle("typologies", t) }, t)))),
      h("div", { class: "row" }, num("price_max", f.mode === "sale" ? "Preço máx. (€)" : "Renda máx. (€)", "Sem limite"), num("area_min", "Área mín. (m²)", "0")),
      h("fieldset", { style: { border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "4px" } },
        h("legend", { class: "small", style: { fontWeight: 600, padding: 0, marginBottom: "6px" } }, "Extras"),
        EXTRAS.map((x) => h("label", { class: "check" }, h("input", { type: "checkbox", checked: f.extras.includes(x), onchange: toggle("extras", x) }), x)),
        h("span", { class: "xsmall muted" }, "Lidos da página de detalhe; anúncios ainda sem detalhe ficam de fora.")),
      h("fieldset", { style: { border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "8px" } },
        h("legend", { class: "small", style: { fontWeight: 600, padding: 0, marginBottom: "6px" } }, "Tempo até ao trabalho"),
        h("div", { class: "row" },
          h("select", { "aria-label": "Minutos máximos", onchange: (e) => update(f, { max_commute: e.target.value }) },
            [["", "Sem limite"], ["15", "Até 15 min"], ["30", "Até 30 min"], ["45", "Até 45 min"], ["60", "Até 60 min"]].map(([v, l]) => h("option", { value: v, selected: f.max_commute === v }, l))),
          h("select", { "aria-label": "Para quem", onchange: (e) => update(f, { commute_who: e.target.value }) },
            [["both", "Para os dois"], ["a", "Para " + App.userName("a")], ["b", "Para " + App.userName("b")]].map(([v, l]) => h("option", { value: v, selected: f.commute_who === v }, l)))),
        h("div", { class: "seg fill", role: "group", "aria-label": "Meio" }, [["transit", "Transportes"], ["car", "Carro"]].map(([m, l]) =>
          h("button", { type: "button", "aria-pressed": f.commute_mode === m ? "true" : "false", onclick: () => update(f, { commute_mode: m }) }, l)))),
      h("label", { class: "check good" }, h("input", { type: "checkbox", checked: f.below, onchange: (e) => update(f, { below: e.target.checked }) }), "Só abaixo da mediana da zona"),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: f.drops, onchange: (e) => update(f, { drops: e.target.checked }) }), "Só com baixas de preço"),
      f.mode === "rent" ? h("div", { style: { display: "flex", flexDirection: "column", gap: "4px" } },
        h("label", { class: "check" }, h("input", { type: "checkbox", checked: f.midstay, onchange: (e) => update(f, { midstay: e.target.checked }) }),
          "Incluir média duração" + (facets.midstay ? " · " + nf(facets.midstay) : "")),
        h("span", { class: "xsmall muted" }, "Uniplaces, Spacest e UKIO: mobilados, ao mês, cerca de 50% mais caros. Ficam sempre fora das medianas.")) : null,
      sourceFilter(f, facets.sources || {}));
  }

  /* Which portals to show. A flat on two portals counts for both. */
  function sourceFilter(f, counts) {
    const names = Object.keys(counts);
    if (names.length < 2) return null;
    const toggle = (s) => () => { const a = new Set(f.sources); a.has(s) ? a.delete(s) : a.add(s); update(f, { sources: [...a] }); };
    return h("fieldset", { style: { border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: "8px" } },
      h("legend", { class: "small", style: { fontWeight: 600, padding: 0, marginBottom: "6px" } }, "Portais"),
      h("div", { class: "chips", role: "group", "aria-label": "Portais" }, names.map((s) =>
        h("button", { type: "button", class: "chip", "aria-pressed": f.sources.includes(s) ? "true" : "false", onclick: toggle(s) }, App.portal(s) + " · " + nf(counts[s])))),
      h("span", { class: "xsmall muted" }, "Sem nenhum escolhido, mostra todos. O mesmo imóvel em vários portais aparece uma vez."));
  }

  function carFilters(f, models) {
    const toggleBody = (v) => () => update(f, { body: f.body === v ? "" : v });
    return h("div", { style: { display: "flex", flexDirection: "column", gap: "20px" } },
      h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } }, h("div", { class: "small", style: { fontWeight: 600 } }, "Tipo"),
        h("div", { class: "chips", role: "group", "aria-label": "Tipo de carro" }, CAR_TYPES.map(([v, l]) =>
          h("button", { type: "button", class: "chip sq", "aria-pressed": f.body === v ? "true" : "false", onclick: toggleBody(v) }, l)))),
      h("label", { class: "field" }, "Modelo", h("select", { onchange: (e) => update(f, { model_id: e.target.value }) },
        h("option", { value: "" }, "Todos os seguidos"), (models || []).map((m) => h("option", { value: m.id, selected: String(m.id) === f.model_id }, m.label)))),
      h("label", { class: "field" }, "Combustível", h("select", { onchange: (e) => update(f, { fuel: e.target.value }) },
        FUELS.map(([v, l]) => h("option", { value: v, selected: f.fuel === v }, l)))),
      h("div", { class: "row" },
        h("label", { class: "field" }, "Ano mínimo", h("select", { onchange: (e) => update(f, { year_min: e.target.value }) },
          ["", "2016", "2018", "2019", "2020", "2021", "2022", "2023"].map((v) => h("option", { value: v, selected: f.year_min === v }, v || "Qualquer")))),
        h("label", { class: "field" }, "Km máximo", h("select", { onchange: (e) => update(f, { km_max: e.target.value }) },
          [["", "Qualquer"], ["30000", "Até 30 000 km"], ["60000", "Até 60 000 km"], ["90000", "Até 90 000 km"], ["120000", "Até 120 000 km"]].map(([v, l]) => h("option", { value: v, selected: f.km_max === v }, l))))),
      h("label", { class: "field" }, "Preço máximo (€)", h("input", { type: "number", inputmode: "numeric", placeholder: "Sem limite", value: f.cprice_max, onchange: (e) => update(f, { cprice_max: e.target.value }) })),
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: f.automatic, onchange: (e) => update(f, { automatic: e.target.checked }) }), "Caixa automática"),
      h("label", { class: "check good" }, h("input", { type: "checkbox", checked: f.good, onchange: (e) => update(f, { good: e.target.checked }) }), "Só bons negócios"));
  }

  App.carCard = (c) => {
    const badge = c.rating === "bom" ? h("span", { class: "pill solid-good badge" }, "Bom negócio · " + nf(c.discount_pct) + "% abaixo")
      : c.rating === "acima" ? h("span", { class: "pill solid-bad badge" }, nf(-c.discount_pct) + "% acima")
      : c.rating === "justo" ? h("span", { class: "pill light badge" }, "Preço justo") : null;
    const photo = App.gallery((c.image_urls && c.image_urls.length ? c.image_urls : [c.thumbnail_url]).filter(Boolean), c.title || "carro");
    if (badge) photo.appendChild(badge);
    photo.appendChild(App.saveButton("car", c.id, c.saved, c.title || "carro"));
    const FUEL = Object.fromEntries(FUELS);
    return h("article", { class: "lcard" }, photo,
      h("div", { class: "body" },
        h("div", { class: "t" }, h("h3", null, h("a", { href: "#/carro/" + c.id }, c.title || c.model_label)), h("div", { class: "price" }, App.eur(c.price_eur))),
        h("div", { class: "small muted" }, [c.year, c.km !== null ? nf(c.km) + " km" : null, FUEL[c.fuel] || c.fuel, c.gearbox === "automatica" ? "Automático" : c.gearbox === "manual" ? "Manual" : null].filter(Boolean).join(" · ")),
        c.peer_med_price ? h("div", { class: "small muted" }, "Semelhantes: " + App.eur(c.peer_med_price) + " (n=" + c.peer_n + ")") : h("div", { class: "small muted" }, "Sem semelhantes suficientes para comparar"),
        h("div", { class: "tags" }, [BODY_LABEL[c.body_type] || c.body_type, c.location].filter(Boolean).map((t) => h("span", null, t))),
        h("div", { class: "foot" },
          h("span", { class: "xsmall muted" }, c.days_listed !== null && c.days_listed !== undefined ? App.days(c.days_listed) + " no mercado" : ""),
          h("a", { class: "btn dark", href: c.url, target: "_blank", rel: "noopener noreferrer" }, "Ver no Standvirtual", App.icon("out", 14)))));
  };

  async function drawMap(container, f) {
    const el = h("div", { class: "map" });
    const legend = h("div", { class: "row wrap", style: { alignItems: "center" } });
    container.appendChild(el);
    container.appendChild(legend);
    const map = App.makeMap(el);
    if (!map) return;
    const pts = await api("listings/geo?" + propertyQuery(f));
    const color = (s) => (s === null ? "#8FA1BA" : s >= 10 ? "#1F6F6B" : s >= 0 ? "#4F6A8F" : "#C2622D");
    for (const [id, lat, lon, p, sc, t, g] of pts.slice(0, 4000)) {
      const m = L.circleMarker([lat, lon], { radius: g === "exact" ? 5 : 3, color: "#fff", weight: 1, fillColor: color(sc), fillOpacity: 0.9 });
      m.bindTooltip((t || "") + " · " + (f.mode === "sale" ? App.eur(p) : App.eur(p) + "/mês") + (sc !== null ? " · " + nf(sc) + "% face aos vizinhos" : ""));
      m.on("click", () => App.go("#/anuncio/" + id));
      m.addTo(map);
    }
    // Isochrones: the grid cells within 15/30/45 min of each workplace.
    const layers = {};
    for (const d of App.state.me.commute.filter((x) => x.cells > 0 && !x.stale)) {
      const btn = h("label", { class: "check" }, h("input", { type: "checkbox", onchange: async (e) => {
        if (!e.target.checked) { if (layers[d.id]) map.removeLayer(layers[d.id]); return; }
        if (!layers[d.id]) layers[d.id] = await isochrone(d);
        layers[d.id].addTo(map);
      } }), App.userName(d.slot) + " · " + (d.mode === "car" ? "carro" : "transportes"));
      legend.appendChild(btn);
    }
    if (legend.childNodes.length) legend.prepend(h("span", { class: "small muted" }, "Isócronas 15 / 30 / 45 min:"));
  }

  async function isochrone(d) {
    const g = await api("commute/" + d.id);
    const st = g.dest;
    const group = L.layerGroup();
    const shade = d.slot === "a" ? ["#22385A", "#4F6A8F", "#8FA1BA"] : ["#8E4219", "#C2622D", "#E9A27C"];
    for (const [la, lo, m] of g.cells) {
      if (m > 45) continue;
      const lat = la * st.step_lat, lon = lo * st.step_lon;
      const c = m <= 15 ? shade[0] : m <= 30 ? shade[1] : shade[2];
      L.rectangle([[lat - st.step_lat / 2, lon - st.step_lon / 2], [lat + st.step_lat / 2, lon + st.step_lon / 2]],
        { stroke: false, fillColor: c, fillOpacity: 0.28, interactive: false }).addTo(group);
    }
    return group;
  }

  App.pages.anuncios = {
    title: "Anúncios",
    async render({ params }) {
      const f = readFilters(params);
      const isIm = f.cat === "imoveis";
      const pc = App.state.me.settings.property_criteria;
      let data, carData;
      if (isIm) {
        data = await api("listings?" + propertyQuery(f, { limit: PAGE }));
      } else {
        carData = await api("cars?" + carQuery(f, { limit: PAGE })).catch(() => ({ total: 0, items: [], models: [] }));
      }
      const totals = isIm ? data.facets.totals : {};
      const root = h("frag");
      add(root, h("div", { class: "page-head" },
        h("div", null, h("h1", null, "Todos os anúncios"),
          h("p", { class: "muted", style: { margin: "6px 0 0", fontSize: "14px" } }, isIm
            ? "Imovirtual, RE/MAX, Casa Sapo, ERA e KW, recolhidos todas as noites · " + pc.councils.join(", ")
            : "Standvirtual, só os modelos que seguem, buscados quando carregam em Atualizar")),
        h("div", { class: "pills", role: "group", "aria-label": "Categoria" },
          h("button", { type: "button", "aria-pressed": isIm ? "true" : "false", onclick: () => update(f, { cat: "imoveis", sort: "" }) }, "Imóveis" + (isIm ? " · " + nf((totals.rent || 0) + (totals.sale || 0)) : "")),
          h("button", { type: "button", "aria-pressed": !isIm ? "true" : "false", onclick: () => update(f, { cat: "carros", sort: "" }) }, "Carros" + (!isIm ? " · " + nf(carData.total) : "")))));

      const results = h("section", { class: "results", "aria-label": "Resultados" });
      const list = isIm ? data.items : carData.items;
      const total = isIm ? data.total : carData.total;
      const sorts = isIm ? SORTS_IM : SORTS_CAR;
      add(results, h("div", { class: "results-head" },
        h("div", null, h("span", { class: "n" }, nf(total)), " ", isIm ? (total === 1 ? "imóvel" : "imóveis") + (f.mode === "sale" ? " à venda" : " para arrendar") : total === 1 ? "carro" : "carros"),
        h("div", { class: "row", style: { alignItems: "center", flex: "0 1 auto" } },
          isIm ? h("div", { class: "seg", role: "group", "aria-label": "Vista" }, [["grid", "Grelha"], ["map", "Mapa"]].map(([v, l]) =>
            h("button", { type: "button", "aria-pressed": f.view === v ? "true" : "false", onclick: () => update(f, { view: v }) }, l))) : null,
          h("label", { class: "sr-only", for: "f-ordem" }, "Ordenar"),
          h("select", { id: "f-ordem", style: { width: "auto" }, onchange: (e) => update(f, { sort: e.target.value }) },
            sorts.map(([v, l]) => h("option", { value: v, selected: (f.sort || sorts[0][0]) === v }, l))))));

      if (isIm && f.view === "map") {
        const box = h("div", { style: { display: "flex", flexDirection: "column", gap: "10px" } });
        results.appendChild(box);
        setTimeout(() => drawMap(box, f), 0);
      } else if (!list.length) {
        results.appendChild(h("div", { class: "empty" }, h("b", null, isIm ? "Nenhum anúncio com estes filtros" : "Sem carros para mostrar"),
          h("span", { class: "muted" }, isIm ? "Alarga o preço ou a zona, ou guarda a pesquisa para seres avisado quando aparecer." : "Sigam um modelo no separador Carros para ver os seus anúncios aqui."),
          isIm ? h("button", { type: "button", class: "btn dark", onclick: () => update(f, { council: "", parish: "", typologies: [], price_max: "", area_min: "", extras: [], below: false, drops: false, max_commute: "", midstay: false, sources: [] }) }, "Limpar filtros")
            : h("a", { class: "btn dark", href: "#/carros" }, "Ir para Carros")));
      } else {
        const grid = h("div", { class: "grid-cards" }, list.map((r) => (isIm ? App.listingCard(r) : App.carCard(r))));
        results.appendChild(grid);
        let offset = list.length;
        if (total > offset) {
          const more = h("button", { type: "button", class: "btn big", onclick: async () => {
            more.disabled = true;
            const next = isIm ? await api("listings?" + propertyQuery(f, { limit: PAGE, offset })) : await api("cars?" + carQuery(f, { limit: PAGE, offset }));
            next.items.forEach((r) => grid.appendChild(isIm ? App.listingCard(r) : App.carCard(r)));
            offset += next.items.length;
            more.disabled = false;
            if (offset >= total) more.remove(); else more.textContent = "Mostrar mais (" + nf(total - offset) + ")";
          } }, "Mostrar mais (" + nf(total - offset) + ")");
          results.appendChild(more);
        }
      }

      const saveSearch = isIm ? h("button", { type: "button", class: "btn big", style: { borderColor: "var(--ink)" }, onclick: async () => {
        const name = prompt("Nome para esta pesquisa (avisamos quando aparecer um anúncio novo):",
          [f.typologies.join("/"), f.parish ? App.short(f.parish) : f.council, f.price_max ? "até " + f.price_max + " €" : ""].filter(Boolean).join(" · ") || "Pesquisa");
        if (!name) return;
        const filters = { mode: f.mode, council: f.council || null, parish: f.parish || null, typologies: f.typologies, price_max: f.price_max || null,
          area_min: f.area_min || null, extras: f.extras, below: f.below, max_commute: f.max_commute || null, commute_who: f.commute_who, commute_mode: f.commute_mode };
        await api("searches", { body: { kind: "listing", name, filters } });
        App.toast("Pesquisa guardada. Os alertas chegam pelo Telegram de quem a guardou.");
      } }, "Guardar pesquisa como alerta") : null;

      add(root, h("div", { class: "with-filters" },
        // On a phone the filters start folded, so results come first.
        h("aside", { class: "filters", "aria-label": "Filtros" }, h("details", { class: "card tight filter-box", open: window.innerWidth > 760 },
          h("summary", { class: "card-head", style: { alignItems: "center", cursor: "pointer", listStyle: "none" } }, h("h2", { style: { fontSize: "22px" } }, "Filtros"),
            h("button", { type: "button", class: "btn link", onclick: (e) => { e.preventDefault(); App.setQuery({ cat: f.cat }); App.render(); } }, "Limpar")),
          isIm ? propertyFilters(f, data.facets) : carFilters(f, carData.models),
          saveSearch)),
        results));
      return root;
    },
  };
})();
