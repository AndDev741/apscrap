/* apScrap front end. Reads data.json (written nightly by `apscrap export`) and
 * does all filtering in the browser. Every string that came from a listing is
 * inserted with textContent, never innerHTML: titles are typed by advertisers. */
"use strict";

const FOCUS_COUNCILS = ["Lisboa", "Odivelas", "Loures"];
const TYPOLOGIES = ["T0", "T1", "T2", "T3", "T4+"];
const TYPES = [["flat", "Apartamento"], ["house", "Moradia"]];
const PAGE = 30;
const STORE_KEY = "apscrap.filters.v1";

const DEFAULTS = {
  councils: [...FOCUS_COUNCILS],
  typologies: ["T1", "T2", "T3", "T4+"],
  types: ["flat", "house"],
  maxPrice: 1300,
  maxCommute: "",
  temp: false,
};

const state = {
  data: null,
  filters: loadFilters(),
  tab: "deals",
  shown: { deals: PAGE, negotiate: PAGE, new: PAGE },
  map: null,
  mapLayer: null,
  tiles: null,
  parishSort: { key: "parish", dir: 1 },
};

// -- small helpers ---------------------------------------------------------

const $ = (sel) => document.querySelector(sel);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k === "style") node.style.cssText = v;
    else node.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children) if (c != null) node.append(c);
  return node;
}

const icon = (name) => el("i", { class: `ph ph-${name}`, "aria-hidden": "true" });

function eur(n) {
  if (n == null) return "";
  return "€" + Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
}

function num(n, digits = 0) {
  return Number(n).toLocaleString("pt-PT", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// export.py strips these shared prefixes to keep data.json small
function listingUrl(l) {
  return l.url.startsWith("http") ? l.url : `https://www.imovirtual.com/pt/anuncio/${l.url}`;
}
function thumbUrl(l) {
  if (!l.thumbnail_url) return null;
  return l.thumbnail_url.startsWith("http") ? l.thumbnail_url
    : `https://ireland.apollo.olxcdn.com/v1/files/${l.thumbnail_url}/image;s=655x491;q=80`;
}

function typologyBucket(t) {
  if (!t) return null;
  const n = parseInt(t.slice(1), 10);
  return n >= 4 ? "T4+" : t;
}

function daysBetween(a, b) {
  return Math.round((new Date(b) - new Date(a)) / 86400000);
}

function loadFilters() {
  try {
    const raw = localStorage.getItem(STORE_KEY);
    if (raw) return { ...DEFAULTS, ...JSON.parse(raw) };
  } catch (_) { /* private mode or blocked storage: defaults are fine */ }
  return structuredClone(DEFAULTS);
}

function saveFilters() {
  try { localStorage.setItem(STORE_KEY, JSON.stringify(state.filters)); } catch (_) { /* ignore */ }
}

// -- filtering -------------------------------------------------------------

function matches(l) {
  const f = state.filters;
  if (!f.councils.includes(l.council)) return false;
  if (!f.typologies.includes(typologyBucket(l.typology))) return false;
  if (!f.types.includes(l.property_type)) return false;
  if (f.maxPrice && l.price_eur > f.maxPrice) return false;
  if (!f.temp && l.is_temporary) return false;
  if (f.maxCommute && (l.commute_min == null || l.commute_min > Number(f.maxCommute))) return false;
  return true;
}

function listedDate(l) {
  return l.listed || l.first_seen;
}

function leverage(l) {
  const dropPct = l.max_price ? (1 - l.price_eur / l.max_price) * 100 : 0;
  return (l.days_on_market || 0) / 30 + (l.n_drops || 0) * 2 + dropPct / 4;
}

function views(list) {
  const today = state.data.generated_at.slice(0, 10);
  const byScore = (a, b) => (b.deal_score ?? -999) - (a.deal_score ?? -999);
  return {
    deals: [...list].sort(byScore),
    negotiate: list
      .filter((l) => (l.days_on_market || 0) >= 60 || (l.n_drops || 0) > 0)
      .sort((a, b) => leverage(b) - leverage(a)),
    new: list.filter((l) => daysBetween(listedDate(l), today) <= 3).sort(byScore),
  };
}

// -- rendering: summary ----------------------------------------------------

function renderSummary(list, v) {
  $("#hero-figure").textContent = num(list.length);
  const f = state.filters;
  const where = f.councils.length <= 3 ? f.councils.join(", ").replace(/, ([^,]*)$/, " e $1") : `${f.councils.length} concelhos`;
  $("#hero-caption").textContent =
    `${list.length === 1 ? "casa para arrendar bate" : "casas para arrendar batem"} com os teus filtros, até ${eur(f.maxPrice)} em ${where || "nenhum concelho"}.`;

  const week = state.data.generated_at.slice(0, 10);
  const drops = list.filter((l) => l.last_drop_on && daysBetween(l.last_drop_on, week) <= 7).length;
  const prices = list.map((l) => l.price_eur).sort((a, b) => a - b);
  const median = prices.length ? prices[Math.floor(prices.length / 2)] : null;

  const tiles = $("#tiles");
  tiles.replaceChildren(
    tile("Publicados nos últimos 3 dias", num(v.new.length)),
    tile("Baixaram o preço esta semana", num(drops)),
    tile("Renda mediana com estes filtros", median ? eur(median) : "sem dados"),
  );
}

function tile(label, value) {
  return el("div", { class: "tile" }, el("dt", { text: label }), el("dd", { text: value }));
}

// -- rendering: cards -------------------------------------------------------

function card(l, { featured = false, mode = "deals", index = 0 } = {}) {
  const a = el("a", {
    class: featured ? "card featured" : "card",
    href: listingUrl(l),
    target: "_blank",
    rel: "noopener noreferrer",
    style: `--i:${Math.min(index, 14)}`,
  });

  const photo = el("div", { class: "photo" });
  if (l.thumbnail_url) {
    const img = el("img", { src: thumbUrl(l), alt: "", loading: index < 6 ? "eager" : "lazy", decoding: "async", referrerpolicy: "no-referrer" });
    img.addEventListener("error", () => img.replaceWith(el("span", { class: "placeholder" }, icon("house-line"))));
    photo.append(img);
  } else {
    photo.append(el("span", { class: "placeholder" }, icon("house-line")));
  }
  a.append(photo);

  const body = el("div", { class: "card-body" });

  const priceRow = el("div", { class: "price-row" },
    el("span", { class: "price" }, eur(l.price_eur), el("small", { text: " /mês" })));
  const b = badge(l, mode);
  if (b) priceRow.append(b);
  body.append(priceRow);

  const area = l.area_sqm ? `${num(l.area_sqm)} m²` : null;
  const typ = l.typology ? `${l.property_type === "house" ? "Moradia" : "Apartamento"} ${l.typology}` : (l.property_type === "house" ? "Moradia" : "Apartamento");
  body.append(el("div", { class: "facts", text: [typ, area].filter(Boolean).join(", ") }));
  body.append(el("div", { class: "place", text: [l.parish, l.council].filter(Boolean).join(", ") }));

  const meta = el("div", { class: "meta" });
  if (l.commute_min != null) {
    const approx = l.geo_precision !== "exact";
    meta.append(el("span", { title: approx ? "Estimado a partir do centro da freguesia" : "Transporte público, dia útil às 8h" },
      icon("train"), `${approx ? "~" : ""}${l.commute_min} min`));
  }
  if (l.days_on_market != null) {
    meta.append(el("span", {}, icon("calendar-blank"), l.days_on_market <= 1 ? "anunciado hoje" : `há ${l.days_on_market} dias`));
  }
  if (l.n_drops > 0) {
    meta.append(el("span", {}, icon("trend-down"), l.n_drops === 1 ? "baixou 1 vez" : `baixou ${l.n_drops} vezes`));
  }
  if (l.is_private_owner) meta.append(el("span", {}, icon("user"), "particular"));
  if (l.is_temporary) meta.append(el("span", {}, icon("clock-countdown"), "temporário"));
  if (l.dupes > 1) meta.append(el("span", {}, icon("buildings"), `${l.dupes} anúncios iguais`));
  body.append(meta);

  if (featured) {
    if (l.peer_med_price) {
      const kind = l.property_type === "house" ? "moradias" : "apartamentos";
      const who = l.peer_level === "council_any" ? `das ${kind}` : `dos ${kind} ${l.typology}`;
      const where = l.peer_level === "parish" ? l.parish : l.council;
      body.append(el("p", { class: "peer", text: `A renda mediana ${who} em ${where} é ${eur(l.peer_med_price)}, entre ${num(l.peer_n)} anúncios.` }));
    }
    const s = sparkline(l.price_path, 160, 36);
    if (s) body.append(s);
  } else if (mode === "negotiate" && l.n_drops > 0) {
    const s = sparkline(l.price_path, 120, 28);
    if (s) body.append(s);
  }

  body.append(el("div", { class: "card-title", text: l.title || "" }));
  a.append(body);
  return a;
}

function badge(l, mode) {
  if (mode === "negotiate" && l.max_price && l.price_eur < l.max_price) {
    const pct = Math.round((1 - l.price_eur / l.max_price) * 100);
    return el("span", { class: "badge", title: `Era ${eur(l.max_price)}` }, icon("arrow-down-right"), `${pct}% mais barato`);
  }
  if (l.deal_score != null && l.deal_score >= 5) {
    return el("span", { class: "badge", title: "Abaixo da mediana dos vizinhos, média entre preço e €/m²" }, `${Math.round(l.deal_score)}% abaixo`);
  }
  if (l.deal_score != null && l.deal_score <= -5) {
    return el("span", { class: "badge plain" }, `${Math.round(-l.deal_score)}% acima`);
  }
  return null;
}

function sparkline(path, w, h) {
  if (!path || path.length < 2) return null;
  const pts = path.map(([d, p]) => [new Date(d).getTime(), p]);
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x0 = Math.min(...xs), x1 = Math.max(...xs) || x0 + 1;
  const y0 = Math.min(...ys), y1 = Math.max(...ys);
  const pad = 5;
  const sx = (x) => pad + ((x - x0) / (x1 - x0 || 1)) * (w - pad * 2);
  const sy = (y) => pad + (1 - (y - y0) / (y1 - y0 || 1)) * (h - pad * 2);
  // stepped: a price holds until the day it changes
  let d = `M${sx(pts[0][0])},${sy(pts[0][1])}`;
  for (let i = 1; i < pts.length; i++) d += ` H${sx(pts[i][0])} V${sy(pts[i][1])}`;
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "spark");
  svg.setAttribute("width", w);
  svg.setAttribute("height", h);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `Preço: ${path.map(([, p]) => eur(p)).join(", depois ")}`);
  const line = document.createElementNS(ns, "path");
  line.setAttribute("d", d);
  line.setAttribute("fill", "none");
  line.setAttribute("stroke", "var(--accent)");
  line.setAttribute("stroke-width", "2");
  line.setAttribute("stroke-linejoin", "round");
  line.setAttribute("stroke-linecap", "round");
  const last = pts[pts.length - 1];
  const dot = document.createElementNS(ns, "circle");
  dot.setAttribute("cx", sx(last[0]));
  dot.setAttribute("cy", sy(last[1]));
  dot.setAttribute("r", "4");
  dot.setAttribute("fill", "var(--accent)");
  dot.setAttribute("stroke", "var(--surface)");
  dot.setAttribute("stroke-width", "2");
  svg.append(line, dot);
  return svg;
}

function renderList(name, list) {
  const host = $(`#list-${name}`);
  if (!list.length) {
    host.replaceChildren(emptyState(name));
    return;
  }
  const n = state.shown[name];
  const nodes = list.slice(0, n).map((l, i) => card(l, { featured: name === "deals" && i === 0, mode: name, index: i }));
  host.replaceChildren(...nodes);
  const panel = host.parentElement;
  panel.querySelector(".more")?.remove();
  if (list.length > n) {
    const more = el("button", { class: "more", type: "button" }, `Mostrar mais ${Math.min(PAGE, list.length - n)} de ${num(list.length - n)}`);
    more.addEventListener("click", () => { state.shown[name] += PAGE; render(); });
    panel.append(more);
  }
}

function emptyState(name) {
  const msg = {
    deals: ["Nada com estes filtros", "Experimenta subir a renda máxima ou juntar outro concelho."],
    negotiate: ["Ainda não há margem para negociar", "Aparecem aqui anúncios com mais de 60 dias ou que baixaram de preço. Com mais noites de histórico esta lista enche."],
    new: ["Nada publicado nos últimos três dias", "Com estes filtros não entrou nenhum anúncio novo. O alerta do Telegram avisa quando entrar."],
  }[name];
  const reset = el("button", { class: "chip", type: "button" }, "Repor os filtros");
  reset.addEventListener("click", () => { state.filters = structuredClone(DEFAULTS); saveFilters(); buildFilters(); render(); });
  return el("div", { class: "empty" }, icon(name === "new" ? "sparkle" : "funnel"),
    el("h3", { text: msg[0] }), el("p", { text: msg[1] }), name === "deals" ? reset : null);
}

// -- rendering: map -----------------------------------------------------------

function scoreColor(s) {
  const css = getComputedStyle(document.documentElement);
  if (s == null || s < 5) return css.getPropertyValue("--neutral-mark").trim();
  if (s < 15) return css.getPropertyValue("--seq-1").trim();
  if (s < 25) return css.getPropertyValue("--seq-2").trim();
  if (s < 35) return css.getPropertyValue("--seq-3").trim();
  return css.getPropertyValue("--seq-4").trim();
}

function isDark() {
  const t = document.documentElement.dataset.theme;
  return t === "dark" || (t !== "light" && matchMedia("(prefers-color-scheme: dark)").matches);
}

function renderMap(list) {
  if (!window.L) return;
  const exact = list.filter((l) => l.geo_precision === "exact" && l.geo_lat != null);
  $("#map-note").textContent = exact.length === list.length
    ? `${num(exact.length)} anúncios no mapa, coloridos pelo desconto face aos vizinhos.`
    : `${num(exact.length)} de ${num(list.length)} anúncios têm coordenadas exatas e estão no mapa. As restantes chegam nas próximas noites, à medida que as páginas de detalhe são lidas.`;

  if (!state.map) {
    state.map = L.map("map", { zoomControl: true, attributionControl: true }).setView([38.77, -9.15], 12);
  }
  // OpenStreetMap's own tiles: free for light personal use with attribution.
  // There is no dark style, so dark mode inverts them in CSS (.map-dark).
  if (!state.tiles) {
    state.tiles = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(state.map);
  }
  $("#map").classList.toggle("map-dark", isDark());
  state.mapLayer?.remove();
  const surface = getComputedStyle(document.documentElement).getPropertyValue("--surface").trim();
  const markers = exact
    .sort((a, b) => (a.deal_score ?? -99) - (b.deal_score ?? -99)) // best on top
    .map((l) => {
      const m = L.circleMarker([l.geo_lat, l.geo_lon], {
        radius: 6, weight: 2, color: surface, fillColor: scoreColor(l.deal_score), fillOpacity: 1,
      });
      m.bindPopup(() => popup(l), { maxWidth: 260 });
      return m;
    });
  state.mapLayer = L.layerGroup(markers).addTo(state.map);
  setTimeout(() => state.map.invalidateSize(), 0);

  const legend = $("#map-legend");
  legend.replaceChildren(
    ...[["35% ou mais abaixo", 40], ["25 a 35%", 30], ["15 a 25%", 20], ["5 a 15%", 10], ["na média ou acima", 0]].map(([label, s]) =>
      el("span", {}, el("i", { class: "sw", style: `background:${scoreColor(s)}` }), label)),
  );
}

function popup(l) {
  const box = el("div");
  box.append(el("div", { class: "pop-price", text: eur(l.price_eur) }));
  box.append(el("div", { text: [l.typology, l.area_sqm ? `${num(l.area_sqm)} m²` : null].filter(Boolean).join(", ") }));
  box.append(el("div", { text: [l.parish, l.council].filter(Boolean).join(", ") }));
  const bits = [];
  if (l.deal_score != null) bits.push(l.deal_score >= 0 ? `${Math.round(l.deal_score)}% abaixo dos vizinhos` : `${Math.round(-l.deal_score)}% acima dos vizinhos`);
  if (l.commute_min != null) bits.push(`${l.commute_min} min até ${state.data.commute?.label || "ao trabalho"}`);
  if (bits.length) box.append(el("div", { text: bits.join(". ") }));
  box.append(el("a", { href: listingUrl(l), target: "_blank", rel: "noopener noreferrer", text: "Abrir no Imovirtual" }));
  return box;
}

// -- rendering: market ----------------------------------------------------------

function seriesColor(i) {
  return getComputedStyle(document.documentElement).getPropertyValue(`--series-${i + 1}`).trim();
}

function renderMarket() {
  const rows = state.data.market_daily.filter((r) => FOCUS_COUNCILS.includes(r.council));
  const days = [...new Set(rows.map((r) => r.day))].sort();
  const chart = $("#market-chart");
  const legend = $("#market-legend");
  legend.replaceChildren(...FOCUS_COUNCILS.map((c, i) =>
    el("span", {}, el("i", { class: "ln", style: `background:${seriesColor(i)}` }), c)));

  renderMarketTable(rows, days);

  if (days.length < 7) {
    const d = state.data.totals.history_days;
    chart.replaceChildren(el("div", { class: "chart-empty", text:
      `Esta linha precisa de pelo menos uma semana de recolha para dizer alguma coisa. Por agora há ${d} ${d === 1 ? "dia" : "dias"} de histórico, e a renda em Lisboa tem sazonalidade forte: só com meses dá para ler tendência.` }));
    return;
  }

  const W = Math.max(chart.clientWidth, 320), H = 260;
  const m = { t: 10, r: 78, b: 26, l: 40 };
  const x = (d) => m.l + (days.indexOf(d) / Math.max(days.length - 1, 1)) * (W - m.l - m.r);
  const vals = rows.map((r) => r.ppm);
  const lo = Math.floor(Math.min(...vals) - 1), hi = Math.ceil(Math.max(...vals) + 1);
  const y = (v) => m.t + (1 - (v - lo) / (hi - lo || 1)) * (H - m.t - m.b);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("height", H);
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Renda mediana por m² ao longo do tempo, por concelho");
  const add = (tag, attrs, text) => {
    const n = document.createElementNS(ns, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text != null) n.textContent = text;
    svg.append(n);
    return n;
  };

  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const v = lo + ((hi - lo) * i) / ticks;
    add("line", { class: "grid-line", x1: m.l, x2: W - m.r, y1: y(v), y2: y(v) });
    add("text", { x: m.l - 8, y: y(v) + 4, "text-anchor": "end" }, `€${num(v)}`);
  }
  const fmtDay = (d) => new Date(d).toLocaleDateString("pt-PT", { day: "numeric", month: "short" });
  [days[0], days[Math.floor(days.length / 2)], days[days.length - 1]].forEach((d, i) =>
    add("text", { x: x(d), y: H - 6, "text-anchor": ["start", "middle", "end"][i] }, fmtDay(d)));

  const bySeries = FOCUS_COUNCILS.map((c) => rows.filter((r) => r.council === c));
  bySeries.forEach((pts, i) => {
    if (!pts.length) return;
    add("path", {
      d: pts.map((p, j) => `${j ? "L" : "M"}${x(p.day)},${y(p.ppm)}`).join(" "),
      fill: "none", stroke: seriesColor(i), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round",
    });
    const last = pts[pts.length - 1];
    add("circle", { cx: x(last.day), cy: y(last.ppm), r: 4, fill: seriesColor(i), stroke: "var(--surface)", "stroke-width": 2 });
    add("text", { class: "label", x: x(last.day) + 10, y: y(last.ppm) + 4 }, `${FOCUS_COUNCILS[i]} €${num(last.ppm, 1)}`);
  });

  const cross = add("line", { x1: 0, x2: 0, y1: m.t, y2: H - m.b, stroke: "var(--muted)", "stroke-width": 1, opacity: 0 });
  const hit = add("rect", { x: m.l, y: 0, width: W - m.l - m.r, height: H, fill: "transparent" });
  const tip = $("#tooltip");
  const move = (ev) => {
    const box = svg.getBoundingClientRect();
    const px = ((ev.clientX - box.left) / box.width) * W;
    const i = Math.round(((px - m.l) / (W - m.l - m.r)) * (days.length - 1));
    const d = days[Math.max(0, Math.min(days.length - 1, i))];
    cross.setAttribute("x1", x(d)); cross.setAttribute("x2", x(d)); cross.setAttribute("opacity", 1);
    tip.replaceChildren(el("div", { class: "tt-head", text: fmtDay(d) }),
      ...FOCUS_COUNCILS.map((c, k) => {
        const r = rows.find((r) => r.council === c && r.day === d);
        return el("div", { class: "tt-row" }, el("i", { class: "key", style: `background:${seriesColor(k)}` }),
          el("b", { text: r ? `€${num(r.ppm, 1)}/m²` : "sem dados" }), el("span", { text: c }));
      }));
    tip.hidden = false;
    tip.style.left = `${Math.min(ev.clientX + 14, innerWidth - 190)}px`;
    tip.style.top = `${ev.clientY + 14}px`;
  };
  hit.addEventListener("pointermove", move);
  hit.addEventListener("pointerleave", () => { tip.hidden = true; cross.setAttribute("opacity", 0); });
  chart.replaceChildren(svg);
}

function renderMarketTable(rows, days) {
  const t = $("#market-table");
  const head = el("tr", {}, el("th", { text: "Dia" }), ...FOCUS_COUNCILS.map((c) => el("th", { class: "num", text: c })));
  const body = [...days].reverse().map((d) => el("tr", {}, el("td", { text: d }),
    ...FOCUS_COUNCILS.map((c) => {
      const r = rows.find((r) => r.council === c && r.day === d);
      return el("td", { class: "num", text: r ? `€${num(r.ppm, 2)} (n=${r.n})` : "" });
    })));
  t.replaceChildren(el("thead", {}, head), el("tbody", {}, ...body));
}

function renderParishes() {
  const f = state.filters;
  const flats = state.data.parishes.filter((p) => p.property_type === "flat" && f.councils.includes(p.council));
  const key = (p) => `${p.council}|${p.parish}`;
  const rows = new Map();
  for (const p of flats) {
    const r = rows.get(key(p)) || { council: p.council, parish: p.parish, T1: null, T2: null, T3: null, ppm: [], n: 0 };
    if (["T1", "T2", "T3"].includes(p.typology)) r[p.typology] = p;
    r.ppm.push([p.median_price_per_sqm, p.n_listings]);
    r.n += p.n_listings;
    rows.set(key(p), r);
  }
  const list = [...rows.values()].map((r) => ({
    ...r,
    // weighted median-of-medians is a rough figure; labelled "aprox." in the header
    ppmAll: r.ppm.reduce((s, [v, n]) => s + v * n, 0) / Math.max(r.n, 1),
  }));

  const { key: sk, dir } = state.parishSort;
  const val = (r) => sk === "parish" ? r.parish : sk === "council" ? r.council
    : sk === "ppm" ? r.ppmAll : sk === "n" ? r.n : (r[sk]?.median_price ?? Infinity);
  list.sort((a, b) => {
    const A = val(a), B = val(b);
    return (typeof A === "string" ? A.localeCompare(B, "pt") : A - B) * dir;
  });

  const cols = [["parish", "Freguesia"], ["council", "Concelho"], ["T1", "T1", true], ["T2", "T2", true], ["T3", "T3", true], ["ppm", "€/m² aprox.", true], ["n", "Anúncios", true]];
  const head = el("tr", {}, ...cols.map(([k, label, isNum]) => {
    const th = el("th", { class: isNum ? "num" : null, scope: "col", tabindex: "0", "aria-sort": sk === k ? (dir > 0 ? "ascending" : "descending") : null, text: label });
    const go = () => { state.parishSort = { key: k, dir: sk === k ? -dir : 1 }; renderParishes(); };
    th.addEventListener("click", go);
    th.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
    return th;
  }));
  const cell = (p) => {
    if (!p) return el("td", { class: "num thin", text: "" });
    return el("td", { class: p.n_listings < 8 ? "num thin" : "num" }, eur(p.median_price), el("span", { class: "n", text: `n=${p.n_listings}` }));
  };
  const body = list.map((r) => el("tr", {},
    el("td", { text: r.parish || "sem freguesia" }), el("td", { class: "sub", text: r.council }),
    cell(r.T1), cell(r.T2), cell(r.T3),
    el("td", { class: "num", text: `€${num(r.ppmAll, 1)}` }), el("td", { class: "num", text: num(r.n) })));
  $("#parish-table").replaceChildren(el("thead", {}, head), el("tbody", {}, ...body));
}

// -- filters UI -------------------------------------------------------------------

function chipGroup(host, options, selected, onToggle) {
  host.replaceChildren(...options.map(([value, label]) => {
    const b = el("button", { class: "chip", type: "button", "aria-pressed": selected.includes(value) ? "true" : "false", text: label });
    b.addEventListener("click", () => onToggle(value));
    return b;
  }));
}

function toggle(arr, v) {
  return arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v];
}

function buildFilters() {
  const f = state.filters;
  const all = [...new Set(state.data.listings.map((l) => l.council).filter(Boolean))];
  const counts = Object.fromEntries(all.map((c) => [c, state.data.listings.filter((l) => l.council === c).length]));
  const extra = f.councils.filter((c) => !FOCUS_COUNCILS.includes(c));
  const shownCouncils = [...FOCUS_COUNCILS, ...extra];

  const councilHost = $("#f-council");
  chipGroup(councilHost, shownCouncils.map((c) => [c, c]), f.councils, (c) => {
    f.councils = toggle(f.councils, c); commit();
  });
  const others = all.filter((c) => !shownCouncils.includes(c)).sort((a, b) => counts[b] - counts[a]);
  if (others.length) {
    const sel = el("select", { class: "chip", "aria-label": "Juntar outro concelho" }, el("option", { value: "", text: "+ concelho" }),
      ...others.map((c) => el("option", { value: c, text: `${c} (${counts[c]})` })));
    sel.addEventListener("change", () => { if (sel.value) { f.councils = [...f.councils, sel.value]; commit(); } });
    councilHost.append(sel);
  }

  chipGroup($("#f-typology"), TYPOLOGIES.map((t) => [t, t]), f.typologies, (t) => { f.typologies = toggle(f.typologies, t); commit(); });
  chipGroup($("#f-type"), TYPES, f.types, (t) => { f.types = toggle(f.types, t); commit(); });

  const price = $("#f-price");
  price.value = f.maxPrice || "";
  price.oninput = () => { f.maxPrice = Number(price.value) || 0; commit(false); };

  const hasCommute = !!state.data.commute;
  $("#field-commute").hidden = !hasCommute;
  if (hasCommute) $("#commute-label").textContent = `Até ${state.data.commute.label}`;
  if (!hasCommute) f.maxCommute = "";
  const commute = $("#f-commute");
  commute.value = f.maxCommute;
  commute.onchange = () => { f.maxCommute = commute.value; commit(); };

  const temp = $("#f-temp");
  temp.checked = f.temp;
  temp.onchange = () => { f.temp = temp.checked; commit(); };
}

let debounce;
function commit(rebuild = true) {
  saveFilters();
  state.shown = { deals: PAGE, negotiate: PAGE, new: PAGE };
  if (rebuild) buildFilters();
  clearTimeout(debounce);
  debounce = setTimeout(render, rebuild ? 0 : 180);
}

// -- tabs -------------------------------------------------------------------------

function setupTabs() {
  const buttons = [...document.querySelectorAll("#tabs [role=tab]")];
  const select = (btn, focus) => {
    for (const b of buttons) {
      const on = b === btn;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
      $(`#tab-${b.dataset.tab}`).hidden = !on;
    }
    state.tab = btn.dataset.tab;
    if (focus) btn.focus();
    try { localStorage.setItem("apscrap.tab", state.tab); } catch (_) { /* ignore */ }
    render();
  };
  buttons.forEach((b, i) => {
    b.addEventListener("click", () => select(b));
    b.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") select(buttons[(i + 1) % buttons.length], true);
      if (e.key === "ArrowLeft") select(buttons[(i - 1 + buttons.length) % buttons.length], true);
    });
  });
  let saved = null;
  try { saved = localStorage.getItem("apscrap.tab"); } catch (_) { /* ignore */ }
  const start = buttons.find((b) => b.dataset.tab === saved);
  if (start) select(start);
}

function setTabCounts(v) {
  for (const name of ["deals", "negotiate", "new"]) {
    const btn = $(`#t-${name}`);
    let c = btn.querySelector(".count");
    if (!c) { c = el("span", { class: "count" }); btn.append(c); }
    c.textContent = num(v[name].length);
  }
}

// -- top level --------------------------------------------------------------------

function render() {
  if (!state.data) return;
  const list = state.data.listings.filter(matches);
  const v = views(list);
  renderSummary(list, v);
  setTabCounts(v);
  if (["deals", "negotiate", "new"].includes(state.tab)) renderList(state.tab, v[state.tab]);
  if (state.tab === "map") renderMap(list);
  if (state.tab === "market") { renderMarket(); renderParishes(); }
}

function renderMeta() {
  const d = state.data;
  const t = d.totals;
  const when = new Date(d.generated_at);
  const hhmm = when.toLocaleTimeString("pt-PT", { hour: "2-digit", minute: "2-digit" });
  const sameDay = when.toDateString() === new Date().toDateString();
  $("#updated").textContent = sameDay ? `Atualizado hoje às ${hhmm}`
    : `Atualizado a ${when.toLocaleDateString("pt-PT", { day: "numeric", month: "short" })}, ${hhmm}`;
  $("#footer").replaceChildren(
    el("p", { text: `${num(t.active_rent)} arrendamentos e ${num(t.active_sale)} vendas ativos no distrito de Lisboa, recolhidos do Imovirtual desde ${new Date(t.history_from).toLocaleDateString("pt-PT", { day: "numeric", month: "long", year: "numeric" })} (${t.history_days} ${t.history_days === 1 ? "dia" : "dias"} de histórico).` }),
    el("p", { text: d.commute ? `Tempo até ${d.commute.label} de transporte público, num dia útil às 8h, calculado com os horários GTFS da AML. "~" quer dizer que o anúncio ainda não tem coordenadas e o tempo vem do centro da freguesia.` : "O tempo de deslocamento aparece depois de carregada a grelha de transportes." }),
    el("p", { text: "Preço pedido não é preço fechado, e um anúncio que desaparece não quer dizer que arrendou." }),
  );
}

function skeletons() {
  $("#list-deals").replaceChildren(...Array.from({ length: 6 }, () => el("div", { class: "card skeleton" })));
}

async function main() {
  skeletons();
  setupTabs();
  try {
    const res = await fetch("data.json", { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = await res.json();
  } catch (err) {
    $("#updated").textContent = "Sem dados";
    $("#tiles").replaceChildren();
    $("#hero-figure").textContent = "";
    $("#list-deals").replaceChildren(el("div", { class: "empty" }, icon("warning-circle"),
      el("h3", { text: "Não consegui carregar os dados" }),
      el("p", { text: `O ficheiro data.json não respondeu (${err.message}). A recolha da noite pode ter falhado; os logs estão no job apscrap-daily do k3s.` })));
    return;
  }
  renderMeta();
  buildFilters();
  render();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => render());
  let resize;
  addEventListener("resize", () => { clearTimeout(resize); resize = setTimeout(() => { if (state.tab === "market") renderMarket(); }, 150); });
}

main();
