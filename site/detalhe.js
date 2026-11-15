/* One advert: everything known about it, both votes, and the visit notes and
   photos (DU-3). Notes and photos are stored in the app's database and served
   only through it. */
(function () {
  "use strict";
  const { h, api, nf, clear, add } = App;
  const CHECKS = {
    listing: [["humidade", "Humidade ou cheiro"], ["luz", "Luz natural"], ["ruido", "Ruído"], ["cozinha", "Cozinha"], ["wc", "Casas de banho"],
      ["predio", "Prédio e escadas"], ["aquecimento", "Aquecimento / isolamento"], ["vizinhanca", "Vizinhança e escola"]],
    car: [["carrocaria", "Carroçaria"], ["interior", "Interior"], ["pneus", "Pneus"], ["motor", "Motor e caixa"], ["isofix", "Isofix e cadeira"],
      ["historico", "Histórico de revisões"], ["test_drive", "Test drive"]],
  };
  const RATING = [["", "–"], ["bom", "Bom"], ["ok", "Aceitável"], ["mau", "Mau"]];
  const FUEL_PT = { gasolina: "Gasolina", gasoleo: "Gasóleo", hibrido: "Híbrido", hibrido_plugin: "Híbrido plug-in", eletrico: "Elétrico", gpl: "GPL" };
  const BODY_PT = { citadino: "Citadino", utilitario: "Utilitário", carrinha: "Carrinha", suv: "SUV", monovolume: "Monovolume", sedan: "Sedan", coupe: "Coupé", cabrio: "Cabrio", outro: "Outro" };

  function notesSection(kind, itemId, notes) {
    const wrap = h("section", { class: "card", "aria-label": "Notas de visita" });
    const list = h("div");
    const draw = () => {
      clear(list);
      if (!notes.length) list.appendChild(h("p", { class: "muted small", style: { margin: 0 } }, "Ainda sem notas. Depois de uma visita, escrevam o que viram, os defeitos e o cheiro a humidade, e juntem fotos."));
      for (const n of notes) {
        const checks = Object.entries(n.checklist || {}).filter(([, v]) => v);
        const labels = Object.fromEntries(CHECKS[kind]);
        list.appendChild(h("div", { class: "vnote" },
          h("div", { class: "who" }, h("span", { class: "avatar slot-" + (n.slot || "a") }, (n.name || "?").charAt(0)),
            (n.name || "") + " · " + (n.visited_on ? "visita a " + App.dateShort(n.visited_on) : App.ago(n.created_at)),
            h("button", { type: "button", class: "btn link", style: { marginLeft: "auto" }, onclick: async () => {
              if (!confirm("Apagar esta nota e as suas fotos?")) return;
              await api("notes/" + n.id, { method: "DELETE" });
              notes = notes.filter((x) => x.id !== n.id);
              draw();
            } }, "Apagar")),
          n.body ? h("div", { style: { whiteSpace: "pre-wrap" } }, n.body) : null,
          checks.length ? h("div", null, checks.map(([k, v]) => h("span", { class: "checklist" }, h("span", { class: "tag" }, (labels[k] || k) + ": " + (RATING.find((r) => r[0] === v) || [, v])[1])))) : null,
          n.photos.length ? h("div", { class: "photos" }, n.photos.map((id) => h("a", { href: "/api/photos/" + id, target: "_blank" }, h("img", { src: "/api/photos/" + id + "?thumb=1", alt: "Foto da visita", loading: "lazy" })))) : null));
      }
    };
    draw();

    const body = h("textarea", { placeholder: "O que viram, defeitos, perguntas para o senhorio…", "aria-label": "Nota" });
    const date = h("input", { type: "date", "aria-label": "Data da visita", value: new Date().toISOString().slice(0, 10) });
    const selects = {};
    const checklist = h("div", { class: "checklist" }, CHECKS[kind].map(([k, l]) => {
      selects[k] = h("select", null, RATING.map(([v, t]) => h("option", { value: v }, t)));
      return h("label", null, l, selects[k]);
    }));
    const files = h("input", { type: "file", accept: "image/*", multiple: true, capture: "environment", "aria-label": "Fotos" });
    const save = h("button", { type: "submit", class: "btn dark" }, "Guardar nota");
    const form = h("form", { style: { display: "flex", flexDirection: "column", gap: "12px" }, onsubmit: async (e) => {
      e.preventDefault();
      save.disabled = true;
      try {
        const checklistVals = Object.fromEntries(Object.entries(selects).map(([k, el]) => [k, el.value]).filter(([, v]) => v));
        const { id } = await api("notes", { body: { kind, item_id: itemId, body: body.value, checklist: checklistVals, visited_on: date.value || null } });
        const photoIds = [];
        for (const f of files.files) {
          const fd = new FormData();
          fd.append("file", f);
          save.textContent = "A enviar fotos (" + (photoIds.length + 1) + "/" + files.files.length + ")…";
          const r = await api("notes/" + id + "/photos", { body: fd });
          photoIds.push(r.id);
        }
        notes = [{ id, body: body.value, checklist: checklistVals, visited_on: date.value, created_at: new Date().toISOString(),
          slot: App.me().slot, name: App.me().name, photos: photoIds }, ...notes];
        body.value = ""; files.value = ""; Object.values(selects).forEach((s) => { s.value = ""; });
        draw();
        App.toast("Nota guardada");
      } catch (err) { App.toast(err.message); }
      save.disabled = false;
      save.textContent = "Guardar nota";
    } }, h("div", { class: "row wrap" }, h("label", { class: "field" }, "Data da visita", date), h("label", { class: "field" }, "Fotos", files)),
      body, h("details", null, h("summary", { class: "small", style: { cursor: "pointer", fontWeight: 600, minHeight: "32px" } }, "Lista de verificação"), checklist), save);
    add(wrap, h("h2", null, "Notas e fotos de visita"), list, h("div", { style: { borderTop: "1px solid var(--line-2)", paddingTop: "16px" } }, form));
    return wrap;
  }

  function fact(k, v) { return v === null || v === undefined || v === "" ? null : h("div", { class: "stat" }, h("div", { class: "k" }, k), h("div", { class: "v", style: { fontSize: "18px" } }, v)); }

  App.pages.anuncio = {
    title: "Anúncio", tab: "anuncios",
    async render({ arg }) {
      const r = await api("listing/" + arg);
      const mode = r.transaction_type;
      const title = (r.typology ? r.typology + " em " : "") + (r.parish || r.council || "");
      const photos = (r.image_urls && r.image_urls.length ? r.image_urls : [r.thumbnail_url]).filter(Boolean);
      const gal = App.gallery(photos, title, "gallery");
      const ine = (r.ine || []).find((x) => x.indicator === (mode === "sale" ? "sale_ppm" : "rent_ppm"));
      const path = (r.price_path || []).map((p) => [p[0], p[1]]);
      const c = r.commute || {};
      const commuteRows = ["a", "b"].map((slot) => {
        const t = c[slot + "_transit"], car = c[slot + "_car"];
        if (t === undefined && car === undefined) return null;
        return h("li", null, h("span", null, App.userName(slot)), h("span", null, (t !== undefined ? t + " min de transportes" : "") + (t !== undefined && car !== undefined ? " · " : "") + (car !== undefined ? car + " min de carro (sem trânsito)" : "")));
      }).filter(Boolean);
      const root = h("frag");
      add(root, h("div", { class: "page-head" },
        h("div", null, h("a", { href: "#/anuncios", class: "small" }, "← Anúncios"),
          h("h1", { style: { marginTop: "6px" } }, title),
          h("p", { class: "muted", style: { margin: "6px 0 0" } }, r.title)),
        h("div", { class: "row", style: { flex: "0 0 auto", alignItems: "center" } }, App.saveButton("listing", r.id, r.saved, title),
          h("a", { class: "btn dark big", href: r.url, target: "_blank", rel: "noopener noreferrer" }, "Ver no Imovirtual", App.icon("out", 14)))));
      add(root, h("section", { class: "split" },
        h("div", { class: "main", style: { display: "flex", flexDirection: "column", gap: "16px" } }, gal,
          h("div", { class: "card" },
            h("div", { class: "zone-main" }, h("div", { class: "big" }, mode === "sale" ? App.eur(r.price_eur) : App.eur(r.price_eur) + "/mês"),
              App.dealBadge(r.discount_pct), r.is_active ? null : h("span", { class: "pill bad" }, "Já não está anunciado")),
            h("p", { style: { margin: 0, fontWeight: 500 } }, App.fairSentence(r)),
            ine ? h("p", { class: "small muted", style: { margin: 0 } }, "INE para " + (r.parish || r.council) + ": " + App.ppm(ine.value, mode) + " (" + (mode === "sale" ? "vendas" : "novos contratos") + ", 12 meses até " + App.dateShort(ine.period_end) + "). Este anúncio: " + App.ppm(r.price_per_sqm, mode) + ".") : null,
            h("div", { class: "facts" }, fact("Área", r.area_sqm ? nf(r.area_sqm) + " m²" : null), fact("€/m²", App.ppm(r.price_per_sqm, mode)), fact("Andar", r.floor),
              fact("Certificado", r.energy_cert), fact("Ano", r.build_year), fact("Caução", r.deposit_eur ? App.eur(r.deposit_eur) : null),
              fact("Anunciante", r.is_private_owner ? "Particular" : r.agency_name || "Agência"), fact("Extras", (r.extras || []).join(", ")))),
          notesSection("listing", r.id, r.notes || [])),
        h("aside", { class: "side", style: { display: "flex", flexDirection: "column", gap: "16px" } },
          h("div", { class: "card tight" }, h("h2", { style: { fontSize: "20px" } }, "Votos"), App.votes("listing", r.id, r.votes)),
          h("div", { class: "card tight" }, h("h2", { style: { fontSize: "20px" } }, "Histórico"),
            h("p", { style: { margin: 0, fontWeight: 500, color: "var(--clay-dark)" } }, App.historyLine(r)),
            path.length > 1 ? App.sparkline(path, "Histórico de preço") : null,
            h("ul", { class: "timeline" }, path.slice().reverse().map((p) => h("li", null, h("span", null, App.dateShort(p[0])), h("b", null, mode === "sale" ? App.eur(p[1]) : App.eur(p[1]) + "/mês")))),
            h("p", { class: "xsmall muted", style: { margin: 0 } }, App.days(r.chain_days_on_market ?? r.days_on_market) + " no mercado" + (r.n_relists ? ", contando " + r.n_relists + " republicação(ões)" : ""))),
          r.chain && r.chain.length > 1 ? h("div", { class: "card tight" }, h("h2", { style: { fontSize: "20px" } }, "Retirado e republicado"),
            h("p", { class: "xsmall muted", style: { margin: 0 } }, "Provavelmente o mesmo imóvel: mesma freguesia, tipologia, área (±2%), posição e título parecido."),
            h("ul", { class: "timeline" }, r.chain.map((x) => h("li", null, h("a", { href: x.url, target: "_blank", rel: "noopener noreferrer" }, App.dateShort(x.first_seen_at) + " a " + App.dateShort(x.last_seen_at)),
              h("span", null, x.last_price ? App.eur(x.last_price) : "")))))
            : null,
          commuteRows.length ? h("div", { class: "card tight" }, h("h2", { style: { fontSize: "20px" } }, "Até ao trabalho"), h("ul", { class: "timeline" }, commuteRows),
            r.geo_precision !== "exact" ? h("p", { class: "xsmall muted", style: { margin: 0 } }, "Sem coordenadas exatas ainda: estimativa a partir do centro da freguesia.") : null) : null)));
      return root;
    },
  };

  App.pages.carro = {
    title: "Carro", tab: "carros",
    async render({ arg }) {
      const c = await api("car/" + arg);
      if (c.error) throw new Error(c.error);
      const gal = App.gallery((c.image_urls && c.image_urls.length ? c.image_urls : [c.thumbnail_url]).filter(Boolean), c.title, "gallery");
      const t = c.tco;
      const root = h("frag");
      add(root, h("div", { class: "page-head" },
        h("div", null, h("a", { href: "#/carros", class: "small" }, "← Carros"), h("h1", { style: { marginTop: "6px" } }, c.title || c.model_label),
          h("p", { class: "muted", style: { margin: "6px 0 0" } }, [c.year, c.km !== null ? nf(c.km) + " km" : null, c.location].filter(Boolean).join(" · "))),
        h("div", { class: "row", style: { flex: "0 0 auto", alignItems: "center" } }, App.saveButton("car", c.id, c.saved, c.title || "carro"),
          h("a", { class: "btn dark big", href: c.url, target: "_blank", rel: "noopener noreferrer" }, "Ver no Standvirtual", App.icon("out", 14)))));
      add(root, h("section", { class: "split" },
        h("div", { class: "main", style: { display: "flex", flexDirection: "column", gap: "16px" } }, gal,
          h("div", { class: "card" },
            h("div", { class: "zone-main" }, h("div", { class: "big" }, App.eur(c.price_eur)),
              c.rating ? h("span", { class: "pill " + (c.rating === "bom" ? "good" : c.rating === "acima" ? "bad" : "neutral") }, c.rating === "bom" ? "Bom negócio" : c.rating === "acima" ? "Acima" : "Preço justo") : null),
            h("p", { style: { margin: 0, fontWeight: 500 } }, c.peer_med_price ? nf(Math.abs(c.discount_pct)) + "% " + (c.discount_pct >= 0 ? "abaixo" : "acima") + " de " + (c.model_label || "") + " semelhantes (" +
              (c.peer_level === "modelo_ano_km" ? "ano ±1, km ±20 000" : "ano ±2") + ", n=" + c.peer_n + "): " + App.eur(c.peer_med_price) : "Sem carros semelhantes suficientes para comparar (precisa de 8)."),
            h("div", { class: "facts" }, fact("Ano", c.year), fact("Km", c.km !== null ? nf(c.km) : null), fact("Combustível", FUEL_PT[c.fuel] || c.fuel), fact("Caixa", c.gearbox === "automatica" ? "Automática" : c.gearbox === "manual" ? "Manual" : c.gearbox),
              fact("Tipo", BODY_PT[c.body_type] || c.body_type), fact("Motor", c.engine_cc ? nf(c.engine_cc) + " cm³" : null), fact("Potência", c.power_hp ? c.power_hp + " cv" : null),
              fact("CO₂", c.co2_gkm ? c.co2_gkm + " g/km" : null), fact("Lugares", c.seats), fact("Vendedor", c.is_private_seller ? "Particular" : "Stand"))),
          notesSection("car", c.id, c.notes || [])),
        h("aside", { class: "side", style: { display: "flex", flexDirection: "column", gap: "16px" } },
          h("div", { class: "card tight" }, h("h2", { style: { fontSize: "20px" } }, "Votos"), App.votes("car", c.id, c.votes)),
          t ? h("div", { class: "card tight" }, h("h2", { style: { fontSize: "20px" } }, "Custo a 5 anos"),
            h("div", { class: "serif", style: { fontSize: "30px", fontWeight: 600 } }, App.eur(t.total), " ", h("span", { class: "small muted", style: { fontFamily: "var(--sans)" } }, App.eur(t.per_month) + "/mês · " + nf(t.per_km, 2) + " €/km")),
            h("ul", { class: "timeline" }, t.parts.map((p) => h("li", null, h("span", null, p.nome), h("b", null, App.eur(p.eur))))),
            h("p", { class: "xsmall muted", style: { margin: 0 } }, "Vale cerca de " + App.eur(t.value_end) + " daqui a 5 anos. IUC " + App.eur(t.iuc_year) + "/ano." + (t.estimates.length ? " Estimativas: " + t.estimates.join(", ") + "." : "")))
            : null,
          c.history && c.history.length > 1 ? h("div", { class: "card tight" }, h("h2", { style: { fontSize: "20px" } }, "Preço quando o vimos"),
            h("ul", { class: "timeline" }, c.history.slice().reverse().map((p) => h("li", null, h("span", null, App.dateShort(p.observed_date)), h("b", null, App.eur(p.price_eur)))))) : null)));
      return root;
    },
  };
})();
