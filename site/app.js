/* Casa & Carro: core. Router, API calls, header and the pieces every page
   shares. No build step: plain scripts in order, everything under App.

   Listing titles and descriptions are written by strangers, so nothing from
   the API goes through innerHTML: h() builds DOM nodes and sets text. */
(function () {
  "use strict";

  const App = (window.App = { pages: {}, state: {} });

  // -- DOM helpers -----------------------------------------------------------
  function h(tag, attrs, ...kids) {
    const el = tag === "frag" ? document.createDocumentFragment() : document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
        else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
        else if (k === "html") throw new Error("no innerHTML");
        else if (v === true) el.setAttribute(k, "");
        else el.setAttribute(k, String(v));
      }
    }
    add(el, kids);
    return el;
  }
  function add(el, ...kids) {
    for (const k of kids.flat(Infinity)) {
      if (k === null || k === undefined || k === false) continue;
      el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
    }
    return el;
  }
  const SVGNS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, ...kids) {
    const el = document.createElementNS(SVGNS, tag);
    for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, String(v));
    for (const k of kids.flat()) if (k) el.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
    return el;
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }
  App.h = h; App.s = s; App.clear = clear; App.add = add;

  // -- formatting ------------------------------------------------------------
  const nfCache = {};
  function nf(n, d = 0) {
    if (n === null || n === undefined || isNaN(n)) return "–";
    const key = d;
    nfCache[key] = nfCache[key] || new Intl.NumberFormat("pt-PT", { minimumFractionDigits: d, maximumFractionDigits: d });
    return nfCache[key].format(Number(n));
  }
  App.nf = nf;
  App.eur = (n, d = 0) => (n === null || n === undefined ? "–" : nf(n, d) + " €");
  App.price = (r) => (r.transaction_type === "sale" || App.state.mode === "sale" && !r.transaction_type ? App.eur(r.price_eur) : App.eur(r.price_eur) + "/mês");
  App.ppm = (v, mode) => (v === null || v === undefined ? "–" : mode === "sale" ? nf(v) + " €/m²" : nf(v, 1) + " €/m²");
  App.days = (d) => (d === null || d === undefined ? "" : d === 1 ? "1 dia" : nf(d) + " dias");
  App.dateShort = (iso) => {
    if (!iso) return "";
    const d = new Date(iso);
    return d.toLocaleDateString("pt-PT", { day: "numeric", month: "short", year: d.getFullYear() === new Date().getFullYear() ? undefined : "numeric" });
  };
  App.ago = (iso) => {
    if (!iso) return "";
    const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
    if (days <= 0) return "hoje";
    if (days === 1) return "ontem";
    if (days < 30) return "há " + days + " dias";
    return App.dateShort(iso);
  };
  App.short = (p) => {
    if (!p) return "";
    let first = p.split(/,| e (?=[A-ZÁÉÍÓÚÂÊÔÃÕ])/)[0].trim();
    first = first.replace(/\bSanta\b/g, "Sta.").replace(/\bSanto\b/g, "Sto.").replace(/\bSão\b/g, "S.");
    if (first === "Parque das Nações") return "P. Nações";
    return first;
  };

  // -- API --------------------------------------------------------------------
  async function api(path, opts = {}) {
    const init = { credentials: "same-origin", headers: {} };
    if (opts.method) init.method = opts.method;
    if (opts.body !== undefined) {
      if (opts.body instanceof FormData) init.body = opts.body;
      else { init.body = JSON.stringify(opts.body); init.headers["Content-Type"] = "application/json"; }
      init.method = init.method || "POST";
    }
    const r = await fetch("/api/" + path, init);
    if (r.status === 401) {
      // The Access session expired: reload so Access can sign us in again.
      location.reload();
      throw new Error("sessão expirada");
    }
    const ct = r.headers.get("content-type") || "";
    const data = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) throw new Error((data && (data.detail || data.error)) || "erro " + r.status);
    return data;
  }
  App.api = api;
  App.qs = (o) => Object.entries(o).filter(([, v]) => v !== null && v !== undefined && v !== "" && !(Array.isArray(v) && !v.length) && v !== false)
    .map(([k, v]) => encodeURIComponent(k) + "=" + encodeURIComponent(Array.isArray(v) ? v.join(",") : v === true ? "1" : v)).join("&");

  let toastTimer;
  App.toast = (msg) => {
    const t = document.getElementById("toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => t.classList.remove("show"), 2800);
  };

  // -- icons (inline stroke SVG) ------------------------------------------------
  const ICONS = {
    spark: ["M12 3l1.8 4.7L18.5 9.5l-4.7 1.8L12 16l-1.8-4.7L5.5 9.5l4.7-1.8z", "M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"],
    heart: ["M12 20s-7-4.4-9-8.6C1.6 8.3 3.6 5 7 5c2 0 3.3 1.1 5 3 1.7-1.9 3-3 5-3 3.4 0 5.4 3.3 4 6.4C19 15.6 12 20 12 20z"],
    left: ["M15 5l-7 7 7 7"], right: ["M9 5l7 7-7 7"], out: ["M7 17L17 7", "M9 7h8v8"],
    send: ["M5 12h14", "M13 6l6 6-6 6"], db: ["M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3z", "M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6", "M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"],
    refresh: ["M20 11a8 8 0 1 0-2.3 5.7", "M20 4v7h-7"], x: ["M6 6l12 12", "M18 6L6 18"], camera: ["M4 8h3l2-3h6l2 3h3v11H4z", "M12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8z"],
  };
  App.icon = (name, size = 18, color = "currentColor", fill = "none") =>
    s("svg", { width: size, height: size, viewBox: "0 0 24 24", fill, stroke: color, "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true", class: name === "spark" ? "spark-ic" : null },
      ...(ICONS[name] || []).map((d) => s("path", { d })));

  // -- shared state -------------------------------------------------------------
  App.state.mode = (() => { try { return localStorage.getItem("cc.mode"); } catch (e) { return null; } })();
  App.setMode = (m) => { App.state.mode = m; try { localStorage.setItem("cc.mode", m); } catch (e) { /* private window */ } };
  App.users = () => (App.state.me && App.state.me.users) || {};
  App.me = () => (App.state.me && App.state.me.user) || {};
  App.other = () => (App.me().slot === "a" ? "b" : "a");
  App.userName = (slot) => (App.users()[slot] && App.users()[slot].name) || (slot === "a" ? "A" : "Parceira");
  App.initial = (slot) => (App.users()[slot] ? App.users()[slot].name.charAt(0).toUpperCase() : "+1");

  async function loadMe() {
    App.state.me = await api("me");
    const pc = App.state.me.settings.property_criteria;
    if (!App.state.mode) App.state.mode = pc.mode || "rent";
    return App.state.me;
  }
  App.reloadMe = loadMe;

  // -- header -------------------------------------------------------------------
  const TABS = [
    ["imoveis", "Imóveis"], ["carros", "Carros"], ["anuncios", "Anúncios"], ["shortlist", "Shortlist"], ["assistente", "Assistente"],
  ];
  function renderHeader(route) {
    const top = clear(document.getElementById("top"));
    const upd = App.state.me && App.state.me.updated_at;
    const tabs = h("nav", { class: "tabs", "aria-label": "Secções" },
      TABS.map(([id, label]) => h("a", { href: "#/" + id, "aria-current": route === id || (route === "comparar" && id === "shortlist") ? "page" : null },
        id === "assistente" ? App.icon("spark", 16) : null, label)));
    const users = App.users();
    const avatars = h("a", { class: "avatars", href: "#/definicoes", "aria-label": "Definições: " + ["a", "b"].map((x) => users[x] ? users[x].name : "por configurar").join(" e ") },
      h("span", { class: "avatar slot-a" }, App.initial("a")),
      h("span", { class: "avatar " + (users.b ? "slot-b" : "avatar-empty") }, App.initial("b")));
    add(top, [
      h("a", { class: "brand", href: "#/imoveis" }, h("b", null, "Casa & Carro"),
        h("span", null, "Painel a dois" + (upd ? " · dados de " + App.dateShort(upd) : ""))),
      tabs,
      h("div", { class: "top-right" }, App.state.headerExtra || null, avatars),
    ]);
  }
  App.renderHeader = renderHeader;

  // -- router -------------------------------------------------------------------
  function parse() {
    const raw = location.hash.replace(/^#\/?/, "") || "imoveis";
    const [path, query] = raw.split("?");
    const parts = path.split("/");
    return { route: parts[0], arg: parts[1], params: new URLSearchParams(query || "") };
  }
  App.route = parse;
  App.go = (hash) => { if (location.hash === hash) render(); else location.hash = hash; };
  App.setQuery = (params, replace = true) => {
    const { route, arg } = parse();
    const q = App.qs(params);
    const hash = "#/" + route + (arg ? "/" + arg : "") + (q ? "?" + q : "");
    if (replace) history.replaceState(null, "", hash); else location.hash = hash;
  };

  let renderSeq = 0;
  async function render() {
    const seq = ++renderSeq;
    const { route, arg, params } = parse();
    const page = App.pages[route] || App.pages.imoveis;
    App.state.headerExtra = null;
    const view = clear(document.getElementById("view"));
    view.appendChild(h("p", { class: "loading" }, "A carregar…"));
    try {
      const node = await page.render({ arg, params, isCurrent: () => seq === renderSeq });
      if (seq !== renderSeq) return;
      renderHeader(page.tab || route);
      clear(view).appendChild(node);
      if (page.after) page.after(node);
      document.title = (page.title ? page.title + " · " : "") + "Casa & Carro";
    } catch (err) {
      if (seq !== renderSeq) return;
      renderHeader(route);
      clear(view).appendChild(h("div", { class: "empty" }, h("b", null, "Não consegui carregar esta página"), h("span", { class: "muted" }, String(err.message || err)),
        h("button", { class: "btn dark", type: "button", onclick: render }, "Tentar outra vez")));
      console.error(err);
    }
  }
  App.render = render;

  App.start = async () => {
    window.addEventListener("hashchange", () => { window.scrollTo(0, 0); render(); });
    try {
      await loadMe();
    } catch (err) {
      clear(document.getElementById("view")).appendChild(h("div", { class: "empty" }, h("b", null, "Sem acesso"), h("span", { class: "muted" }, String(err.message || err))));
      return;
    }
    render();
  };

  // -- shared widgets -------------------------------------------------------------

  /* Badge for how an advert compares with its neighbours (IM-2). */
  App.dealBadge = (pct) => {
    if (pct === null || pct === undefined) return null;
    if (pct >= 5) return h("span", { class: "pill solid-good badge" }, nf(pct) + "% abaixo");
    if (pct <= -5) return h("span", { class: "pill solid-bad badge" }, nf(-pct) + "% acima");
    return h("span", { class: "pill light badge" }, "Preço justo");
  };

  /* "12% abaixo da mediana de T2 em Loures (n=34)" */
  App.fairSentence = (r) => {
    if (r.discount_pct === null || r.discount_pct === undefined || !r.peer_n) return "Sem vizinhos suficientes para comparar (menos de 8 anúncios semelhantes).";
    const where = r.peer_level === "parish" ? App.short(r.parish) : r.council;
    const what = r.peer_level === "council_any" ? (r.property_type === "house" ? "moradias" : "casas") : (r.typology || "casas");
    const pct = Math.abs(r.discount_pct);
    const dir = r.discount_pct >= 0 ? "abaixo" : "acima";
    return nf(pct) + "% " + dir + " da mediana de " + what + " em " + where + " (n=" + r.peer_n + ", por m²)";
  };

  App.historyLine = (r) => {
    const bits = [];
    if (r.n_relists > 0) bits.push("republicado " + (r.n_relists === 1 ? "1 vez" : r.n_relists + " vezes"));
    if (r.n_drops > 0) bits.push(r.n_drops === 1 ? "baixou " + (r.last_drop_on ? App.ago(r.last_drop_on) : "") : "baixou " + r.n_drops + " vezes");
    if (r.max_price && r.price_eur < r.max_price) bits.push("−" + App.eur(r.max_price - r.price_eur) + " no total");
    const d = r.chain_days_on_market ?? r.days_on_market;
    if (!bits.length && d !== null && d !== undefined && d <= 3) return "Novo · " + App.ago(r.first_seen_at || r.listed_at);
    if (!bits.length) return "Sem alterações de preço";
    return bits.join(" · ").replace(/^./, (c) => c.toUpperCase());
  };

  /* Minutes to each person's workplace. */
  App.commuteChips = (c, compact) => {
    if (!c) return null;
    const out = [];
    for (const slot of ["a", "b"]) {
      const t = c[slot + "_transit"], car = c[slot + "_car"];
      if (t === undefined && car === undefined) continue;
      const txt = (compact ? App.initial(slot) + " " : App.userName(slot) + ": ") + (t !== undefined ? t + " min" : "") + (t !== undefined && car !== undefined ? " · " : "") + (car !== undefined ? "🚗 " + car : "");
      out.push(h("span", { class: "slot-" + slot, title: (t !== undefined ? t + " min de transportes" : "") + (car !== undefined ? ", " + car + " min de carro sem trânsito" : "") }, txt));
    }
    return out.length ? h("span", { class: "commute" }, out) : null;
  };

  /* Votes: mine cycles gosto → talvez → não → sem voto; the other's is shown. */
  const VOTE_LABEL = { gosto: "Gosto", talvez: "Talvez", nao: "Não" };
  const NEXT = { undefined: "gosto", gosto: "talvez", talvez: "nao", nao: null };
  App.votes = (kind, id, votes, onChange) => {
    votes = votes || {};
    const me = App.me().slot, other = App.other();
    const wrap = h("div", { class: "votes" });
    const draw = () => {
      clear(wrap);
      const mine = votes[me];
      add(wrap, [
        h("button", { type: "button", class: "vote " + me + (mine === "gosto" ? " on" : ""), "aria-label": "O teu voto: " + (VOTE_LABEL[mine] || "sem voto") + ". Tocar para mudar",
          onclick: async () => {
            const nv = NEXT[mine];
            try {
              await api("vote", { body: { kind, item_id: id, vote: nv } });
              if (nv) votes[me] = nv; else delete votes[me];
              draw();
              if (onChange) onChange(votes);
            } catch (e) { App.toast(e.message); }
          } }, App.initial(me) + " · " + (VOTE_LABEL[mine] || "Votar")),
        h("button", { type: "button", disabled: true, class: "vote " + other + (votes[other] === "gosto" ? " on" : ""), "aria-label": "Voto de " + App.userName(other) + ": " + (VOTE_LABEL[votes[other]] || "sem voto") },
          App.initial(other) + " · " + (VOTE_LABEL[votes[other]] || "Sem voto")),
      ]);
    };
    draw();
    return wrap;
  };

  App.saveButton = (kind, id, saved, label) => {
    const btn = h("button", { type: "button", class: "icon-btn save", "aria-pressed": saved ? "true" : "false" });
    const draw = () => {
      clear(btn).appendChild(App.icon("heart", 20, "#C2622D", saved ? "#C2622D" : "none"));
      btn.setAttribute("aria-pressed", saved ? "true" : "false");
      btn.setAttribute("aria-label", (saved ? "Remover " : "Guardar ") + label + (saved ? " da shortlist" : " na shortlist"));
    };
    btn.addEventListener("click", async () => {
      try {
        await api("shortlist", { body: { kind, item_id: id, saved: !saved } });
        saved = !saved;
        draw();
        App.toast(saved ? "Guardado na shortlist" : "Removido da shortlist");
      } catch (e) { App.toast(e.message); }
    });
    draw();
    return btn;
  };

  /* Photo with prev/next over the advert's own photos. */
  App.gallery = (urls, alt, cls) => {
    urls = (urls || []).filter(Boolean);
    let i = 0;
    const img = urls.length ? h("img", { src: urls[0], alt, loading: "lazy", referrerpolicy: "no-referrer" }) : null;
    const count = urls.length > 1 ? h("span", { class: "count" }, "1 de " + urls.length) : null;
    const move = (d) => (e) => {
      e.preventDefault();
      i = (i + d + urls.length) % urls.length;
      img.src = urls[i];
      count.textContent = i + 1 + " de " + urls.length;
    };
    return h("div", { class: cls || "photo" }, img || h("span", { class: "xsmall muted" }, "Sem foto"),
      urls.length > 1 ? h("button", { type: "button", class: "icon-btn prev", "aria-label": "Foto anterior", onclick: move(-1) }, App.icon("left")) : null,
      urls.length > 1 ? h("button", { type: "button", class: "icon-btn next", "aria-label": "Foto seguinte", onclick: move(1) }, App.icon("right")) : null,
      count);
  };

  App.listingCard = (r, opts = {}) => {
    const mode = r.transaction_type || App.state.mode;
    const title = (r.typology ? r.typology + " · " : "") + App.short(r.parish || r.council || "");
    const photos = (r.image_urls && r.image_urls.length ? r.image_urls : [r.thumbnail_url]).filter(Boolean);
    const photo = App.gallery(photos, title);
    const badge = App.dealBadge(r.discount_pct);
    if (badge) photo.appendChild(badge);
    photo.appendChild(App.saveButton("listing", r.id, r.saved, title));
    const tags = [...(r.extras || [])];
    if (r.is_temporary) tags.unshift("Temporário");
    if (r.property_type === "house") tags.unshift("Moradia");
    const d = r.chain_days_on_market ?? r.days_on_market;
    return h("article", { class: "lcard" + (opts.compact ? " deal-card" : "") },
      photo,
      h("div", { class: "body" },
        h("div", { class: "t" }, h("h3", null, h("a", { href: "#/anuncio/" + r.id }, title)), h("div", { class: "price" }, mode === "sale" ? App.eur(r.price_eur) : App.eur(r.price_eur) + "/mês")),
        h("div", { class: "small muted" }, (r.area_sqm ? nf(r.area_sqm) + " m² · " : "") + App.ppm(r.price_per_sqm, mode) + (mode === "rent" ? " por mês" : "")),
        r.peer_med_ppm ? h("div", { class: "small muted" }, "Mediana da zona: " + App.ppm(r.peer_med_ppm, mode) + " (n=" + r.peer_n + ")") : null,
        tags.length && !opts.compact ? h("div", { class: "tags" }, tags.slice(0, 5).map((t) => h("span", null, t))) : null,
        h("div", { class: "hist" }, App.historyLine(r)),
        opts.votes ? App.votes("listing", r.id, r.votes) : null,
        h("div", { class: "foot" },
          h("span", { class: "xsmall muted" }, d !== null && d !== undefined ? App.days(d) + " no mercado" : "", " ", App.commuteChips(r.commute, true)),
          h("a", { class: "btn dark", href: r.url, target: "_blank", rel: "noopener noreferrer" }, "Ver no Imovirtual", App.icon("out", 14)))));
  };

  /* Sparkline from [[date, value], ...]. */
  App.sparkline = (points, aria) => {
    const svg = s("svg", { viewBox: "0 0 240 64", preserveAspectRatio: "none", role: "img", "aria-label": aria || "evolução", class: "spark" });
    if (!points || points.length < 2) return svg;
    const vals = points.map((p) => Number(p[1]));
    const mn = Math.min(...vals), mx = Math.max(...vals), rg = mx - mn || 1;
    const xy = vals.map((v, i) => [6 + i * (228 / (vals.length - 1)), 58 - ((v - mn) / rg) * 50]);
    const line = xy.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
    svg.appendChild(s("path", { d: line + " L234 64 L6 64 Z", fill: "#E6E9EE" }));
    svg.appendChild(s("path", { d: line, fill: "none", stroke: "#22385A", "stroke-width": 2, "vector-effect": "non-scaling-stroke", "stroke-linejoin": "round" }));
    return svg;
  };

  /* Small safe markdown for the agent's answers: paragraphs, lists, **bold**
     and links to app routes or http(s). Everything else stays text. */
  App.markdown = (text) => {
    const out = h("div", { class: "text" });
    const blocks = String(text || "").split(/\n{2,}/);
    const inline = (str) => {
      const frag = document.createDocumentFragment();
      const re = /(\*\*([^*]+)\*\*)|(\[([^\]]+)\]\(([^)\s]+)\))/g;
      let last = 0, m;
      while ((m = re.exec(str))) {
        if (m.index > last) frag.appendChild(document.createTextNode(str.slice(last, m.index)));
        if (m[1]) frag.appendChild(h("strong", null, m[2]));
        else {
          const href = m[5];
          if (/^#\//.test(href) || /^https?:\/\//.test(href)) frag.appendChild(h("a", { href, target: href.startsWith("#") ? null : "_blank", rel: "noopener noreferrer" }, m[4]));
          else frag.appendChild(document.createTextNode(m[4]));
        }
        last = re.lastIndex;
      }
      if (last < str.length) frag.appendChild(document.createTextNode(str.slice(last)));
      return frag;
    };
    for (const b of blocks) {
      const lines = b.split("\n");
      if (lines.every((l) => /^\s*([-*•]|\d+\.)\s+/.test(l))) {
        const ordered = /^\s*\d+\./.test(lines[0]);
        out.appendChild(h(ordered ? "ol" : "ul", null, lines.map((l) => h("li", null, inline(l.replace(/^\s*([-*•]|\d+\.)\s+/, ""))))));
      } else {
        const p = h("p");
        lines.forEach((l, i) => { if (i) p.appendChild(h("br")); p.appendChild(inline(l.replace(/^#+\s*/, ""))); });
        out.appendChild(p);
      }
    }
    return out;
  };

  /* A map with OpenStreetMap tiles. */
  App.makeMap = (el, center = [38.76, -9.15], zoom = 11) => {
    if (!window.L) { el.appendChild(h("p", { class: "muted small" }, "O mapa não carregou.")); return null; }
    const map = L.map(el, { scrollWheelZoom: false }).setView(center, zoom);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(map);
    return map;
  };
})();
