/* Definições (ST-1): everything personal, stored in the app's database and
   never in git. People, workplaces, what you are looking for, alerts and the
   assistant's monthly budget. */
(function () {
  "use strict";
  const { h, api, nf, clear, add } = App;
  const TYPS = ["T0", "T1", "T2", "T3", "T4+"];
  const BODIES = [["citadino", "Citadino"], ["utilitario", "Utilitário"], ["carrinha", "Carrinha"], ["suv", "SUV"], ["monovolume", "Monovolume"], ["sedan", "Sedan"]];
  const FUELS = [["gasolina", "Gasolina"], ["gasoleo", "Gasóleo"], ["hibrido", "Híbrido"], ["hibrido_plugin", "Híbrido plug-in"], ["eletrico", "Elétrico"], ["gpl", "GPL"]];
  const COUNCILS = ["Lisboa", "Odivelas", "Loures", "Amadora", "Oeiras", "Sintra", "Cascais", "Vila Franca de Xira", "Mafra"];

  const num = (v) => (v === "" || v === null || v === undefined ? null : Number(v));
  function chipSet(options, selected) {
    const set = new Set(selected || []);
    const el = h("div", { class: "chips" }, options.map(([v, l]) => {
      const b = h("button", { type: "button", class: "chip sq", "aria-pressed": set.has(v) ? "true" : "false" }, l);
      b.addEventListener("click", () => { set.has(v) ? set.delete(v) : set.add(v); b.setAttribute("aria-pressed", set.has(v) ? "true" : "false"); });
      return b;
    }));
    el.values = () => [...set];
    return el;
  }
  async function save(key, value, btn) {
    btn.disabled = true;
    try { await api("settings/" + key, { method: "PUT", body: value }); await App.reloadMe(); App.toast("Guardado"); }
    catch (e) { App.toast(e.message); }
    btn.disabled = false;
  }

  function people(me) {
    const card = h("section", { class: "card" }, h("h2", null, "Vocês os dois"),
      h("p", { class: "sub", style: { margin: 0 } }, "O email tem de estar também na política do Cloudflare Access, que é quem deixa entrar na app. Quem entrar primeiro com um email desta lista fica com esse lugar."));
    for (const slot of ["a", "b"]) {
      const u = me.users[slot] || {};
      const name = h("input", { type: "text", value: u.name || "", placeholder: slot === "a" ? "Nome" : "Nome dela", "aria-label": "Nome" });
      const email = h("input", { type: "email", value: u.email || "", placeholder: "email@exemplo.pt", "aria-label": "Email" });
      const btn = h("button", { type: "button", class: "btn" }, "Guardar");
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try { await api("users/" + slot, { method: "PUT", body: { name: name.value, email: email.value } }); await App.reloadMe(); App.toast("Guardado"); App.renderHeader("definicoes"); }
        catch (e) { App.toast(e.message); }
        btn.disabled = false;
      });
      add(card, h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
        h("div", { class: "row", style: { alignItems: "center" } }, h("span", { class: "avatar slot-" + slot, style: { flex: "0 0 auto" } }, (u.name || "?").charAt(0)),
          h("b", null, slot === "a" ? "Pessoa A" : "Pessoa B", u.id === me.user.id ? " (tu)" : "")),
        h("div", { class: "row wrap" }, name, email, h("div", { style: { flex: "0 0 auto" } }, btn))));
    }
    return card;
  }

  function workplaces(me) {
    const card = h("section", { class: "card" }, h("h2", null, "Locais de trabalho"),
      h("p", { class: "sub", style: { margin: 0 } }, "Escolham a pessoa e toquem no mapa. Os tempos vêm de uma grelha calculada no portátil (transportes da AML e OpenStreetMap). Mudar o ponto deixa a grelha desatualizada até alguém a recalcular com scripts/commute_grid.py."));
    const status = h("div", { style: { display: "flex", flexDirection: "column", gap: "6px" } });
    for (const d of me.commute) {
      status.appendChild(h("div", { class: "status-line " + (d.stale || !d.cells ? "warn" : "ok") },
        App.userName(d.slot) + " · " + (d.mode === "car" ? "carro" : "transportes") + " · " + d.label + ": " +
        (d.stale ? "ponto mudou, falta recalcular a grelha" : d.cells ? nf(d.cells) + " células, carregada " + App.ago(d.loaded_at) : "sem grelha")));
    }
    let who = "a";
    const label = h("input", { type: "text", placeholder: "Nome do sítio (ex.: Escritório)", "aria-label": "Nome do local" });
    const whoSel = h("div", { class: "seg", role: "group", "aria-label": "Pessoa" }, ["a", "b"].map((s) =>
      h("button", { type: "button", "aria-pressed": s === who ? "true" : "false", onclick: (e) => { who = s; whoSel.querySelectorAll("button").forEach((b) => b.setAttribute("aria-pressed", "false")); e.currentTarget.setAttribute("aria-pressed", "true"); } }, App.userName(s))));
    const mapEl = h("div", { class: "map small" });
    add(card, status, h("div", { class: "row wrap", style: { alignItems: "center" } }, whoSel, label), mapEl);
    setTimeout(() => {
      const map = App.makeMap(mapEl, [38.74, -9.16], 12);
      if (!map) return;
      const markers = {};
      for (const d of me.commute) {
        if (d.lat && d.lon && !markers[d.slot]) {
          markers[d.slot] = L.circleMarker([d.lat, d.lon], { radius: 9, color: "#fff", weight: 2, fillColor: d.slot === "a" ? "#22385A" : "#C2622D", fillOpacity: 1 }).addTo(map).bindTooltip(App.userName(d.slot) + " · " + d.label);
        }
      }
      map.on("click", async (e) => {
        const name = label.value.trim() || "Trabalho";
        if (!confirm("Guardar o local de trabalho de " + App.userName(who) + " aqui (" + e.latlng.lat.toFixed(4) + ", " + e.latlng.lng.toFixed(4) + ")?")) return;
        try {
          await api("workplace/" + who, { method: "PUT", body: { label: name, lat: e.latlng.lat, lon: e.latlng.lng } });
          await App.reloadMe();
          App.toast("Guardado. A grelha fica por recalcular.");
          App.render();
        } catch (err) { App.toast(err.message); }
      });
    }, 0);
    return card;
  }

  function propertyCriteria(me) {
    const pc = me.settings.property_criteria;
    const mode = h("select", null, [["rent", "Arrendar"], ["sale", "Comprar"]].map(([v, l]) => h("option", { value: v, selected: pc.mode === v }, l)));
    const max = h("input", { type: "number", value: pc.max_price ?? "", inputmode: "numeric" });
    const commute = h("input", { type: "number", value: pc.max_commute ?? "", placeholder: "sem limite", inputmode: "numeric" });
    const typs = chipSet(TYPS.map((t) => [t, t]), (pc.typologies || []).map((t) => (["T4", "T5", "T6"].includes(t) ? "T4+" : t)));
    const councils = chipSet(COUNCILS.map((c) => [c, c]), pc.councils);
    const btn = h("button", { type: "submit", class: "btn dark" }, "Guardar");
    return h("section", { class: "card" }, h("h2", null, "O que procuram em casa"),
      h("form", { onsubmit: (e) => { e.preventDefault(); save("property_criteria", { ...pc, mode: mode.value, max_price: num(max.value), max_commute: num(commute.value),
        typologies: typs.values().flatMap((t) => (t === "T4+" ? ["T4", "T5", "T6"] : [t])), councils: councils.values() }, btn); } },
        h("div", { class: "row wrap" }, h("label", { class: "field" }, "Plano", mode), h("label", { class: "field" }, "Orçamento (€/mês ou €)", max), h("label", { class: "field" }, "Minutos máx. até ao trabalho", commute)),
        h("div", { class: "field" }, "Tipologias", typs), h("div", { class: "field" }, "Concelhos", councils),
        h("p", { class: "xsmall muted", style: { margin: 0 } }, "Estes critérios definem o mapa, o alerta da manhã e o que o assistente assume. Novos concelhos entram no INE na importação seguinte."), btn));
  }

  function carCriteria(me) {
    const cc = me.settings.car_criteria;
    const f = (k, label, ph) => { const el = h("input", { type: "number", value: cc[k] ?? "", placeholder: ph || "", inputmode: "decimal" }); el.dataset.k = k; return [el, h("label", { class: "field" }, label, el)]; };
    const fields = [f("max_price", "Orçamento (€)"), f("min_year", "Ano mínimo"), f("max_km", "Km máximo"), f("min_seats", "Lugares mínimos"),
      f("km_per_year", "Km por ano"), f("insurance_eur", "Cotação de seguro (€/ano)", "estimativa"), f("electricity_eur_kwh", "Eletricidade (€/kWh)")];
    const bodies = chipSet(BODIES, cc.body_types);
    const fuels = chipSet(FUELS, cc.fuels);
    const auto = h("input", { type: "checkbox", checked: !!cc.automatic });
    const isofix = h("input", { type: "checkbox", checked: !!cc.isofix });
    const btn = h("button", { type: "submit", class: "btn dark" }, "Guardar");
    return h("section", { class: "card" }, h("h2", null, "O que procuram no carro"),
      h("form", { onsubmit: (e) => { e.preventDefault(); const v = { ...cc }; fields.forEach(([el]) => { v[el.dataset.k] = num(el.value); });
        Object.assign(v, { body_types: bodies.values(), fuels: fuels.values(), automatic: auto.checked, isofix: isofix.checked }); save("car_criteria", v, btn); } },
        h("div", { class: "row wrap" }, fields.slice(0, 4).map((x) => x[1])), h("div", { class: "row wrap" }, fields.slice(4).map((x) => x[1])),
        h("div", { class: "field" }, "Tipos", bodies), h("div", { class: "field" }, "Combustíveis (vazio = todos)", fuels),
        h("label", { class: "check" }, auto, "Só caixa automática"), h("label", { class: "check" }, isofix, "Precisa de Isofix (cadeira da criança)"),
        h("p", { class: "xsmall muted", style: { margin: 0 } }, "A cotação de seguro e os km por ano entram no custo a 5 anos. Sem cotação, usamos uma estimativa para condutores jovens."), btn));
  }

  function alerts(me, searches) {
    const t = me.settings.telegram, za = me.settings.zone_alert;
    const chatA = h("input", { type: "text", value: t.chat_a || "", placeholder: "chat id" });
    const chatB = h("input", { type: "text", value: t.chat_b || "", placeholder: "chat id" });
    const pct = h("input", { type: "number", value: za.drop_pct, step: "0.5" });
    const weeks = h("input", { type: "number", value: za.weeks });
    const b1 = h("button", { type: "button", class: "btn" }, "Guardar Telegram");
    b1.addEventListener("click", () => save("telegram", { chat_a: chatA.value || null, chat_b: chatB.value || null }, b1));
    const b2 = h("button", { type: "button", class: "btn" }, "Guardar alerta de zona");
    b2.addEventListener("click", () => save("zone_alert", { drop_pct: num(pct.value), weeks: num(weeks.value) }, b2));
    const list = h("ul", { class: "timeline" }, searches.map((s) => h("li", null,
      h("span", null, s.name, h("span", { class: "muted small" }, " · " + (s.owner || ""))),
      h("button", { type: "button", class: "btn link", onclick: async (e) => { await api("searches/" + s.id, { method: "DELETE" }); e.target.closest("li").remove(); } }, "Apagar"))));
    return h("section", { class: "card", id: "alertas" }, h("h2", null, "Alertas"),
      h("p", { class: "sub", style: { margin: 0 } }, "Avisamos uma vez por acontecimento: baixa de preço num guardado, anúncio novo numa pesquisa guardada, zona que caiu mais do que o limite. Chegam pelo Telegram quando o bot está configurado no servidor."),
      h("div", { class: "row wrap" }, h("label", { class: "field" }, "Telegram de " + App.userName("a"), chatA), h("label", { class: "field" }, "Telegram de " + App.userName("b"), chatB)), b1,
      h("div", { class: "row wrap" }, h("label", { class: "field" }, "Zona caiu mais de (%)", pct), h("label", { class: "field" }, "em quantas semanas", weeks)), b2,
      h("div", { class: "field" }, "Pesquisas guardadas"), searches.length ? list : h("p", { class: "muted small", style: { margin: 0 } }, "Nenhuma. Em Anúncios, filtrem e carreguem em “Guardar pesquisa como alerta”."));
  }

  function agentBox(me, st) {
    const a = me.settings.agent;
    const budget = h("input", { type: "number", value: a.budget_eur, step: "1" });
    const pin = h("input", { type: "number", value: a.price_in_eur_mtok, step: "0.1" });
    const pout = h("input", { type: "number", value: a.price_out_eur_mtok, step: "0.1" });
    const btn = h("button", { type: "submit", class: "btn dark" }, "Guardar");
    return h("section", { class: "card" }, h("h2", null, "Assistente"),
      h("div", { class: "status-line " + (st.spent_eur >= st.budget_eur ? "warn" : "ok") }, "Gasto este mês: " + nf(st.spent_eur, 2) + " € de " + nf(st.budget_eur) + " €"),
      h("form", { onsubmit: (e) => { e.preventDefault(); save("agent", { budget_eur: num(budget.value), price_in_eur_mtok: num(pin.value), price_out_eur_mtok: num(pout.value) }, btn); } },
        h("label", { class: "field" }, "Limite mensal (€)", budget),
        h("div", { class: "row wrap" }, h("label", { class: "field" }, "€ por milhão de tokens de entrada", pin), h("label", { class: "field" }, "€ por milhão de tokens de saída", pout)),
        h("p", { class: "xsmall muted", style: { margin: 0 } }, "O gateway escolhe o modelo, por isso estes preços são um teto para o limite ser honesto. Passado o limite, o chat avisa e para."), btn));
  }

  App.pages.definicoes = {
    title: "Definições", tab: "definicoes",
    async render() {
      const me = await App.reloadMe();
      const [searches, st] = await Promise.all([api("searches"), api("agent/status")]);
      const root = h("frag");
      add(root, h("div", { class: "page-head" }, h("div", null, h("h1", null, "Definições"),
        h("p", { class: "muted", style: { margin: "6px 0 0" } }, "Tudo o que é pessoal fica na base de dados da app e nunca no repositório público."))));
      add(root, h("div", { class: "settings" }, people(me), workplaces(me), propertyCriteria(me), carCriteria(me), alerts(me, searches), agentBox(me, st)));
      return root;
    },
  };
})();
