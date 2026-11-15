/* Assistente (AG-1): questions about the data in Portuguese. Answers come from
   the agent's tools only, with the source and a small chart when it has a list
   of comparable numbers. Insights on the right come from the weekly diary and
   the live feed, not from the model. */
(function () {
  "use strict";
  const { h, api, nf, clear, add } = App;
  const SUGGESTIONS = [
    "Onde compensa mais comprar do que arrendar?",
    "Que T2 até 1300 € estão abaixo da mediana e a menos de 40 min para os dois?",
    "Que anúncios da shortlist baixaram de preço?",
    "Que zonas estão a arrefecer?",
    "Que carro familiar desvaloriza menos?",
  ];
  let conversation = null;
  let messages = [];
  const COLORS = ["#1F6F6B", "#4F6A8F", "#C2622D"];

  function botMsg(m) {
    const content = h("div", { class: "content" }, App.markdown(m.text));
    if (m.chart && m.chart.linhas && m.chart.linhas.length) {
      const vals = m.chart.linhas.map((l) => Number(l.valor));
      const max = Math.max(...vals.map(Math.abs)) || 1;
      const unit = m.chart.unidade || "";
      const dec = Math.abs(max) < 100 ? 1 : 0;
      content.appendChild(h("div", { class: "msg-rows" }, h("div", { class: "xsmall muted", style: { textTransform: "uppercase", letterSpacing: ".06em" } }, m.chart.titulo),
        m.chart.linhas.map((l, i) => h("div", { class: "r" }, h("span", { title: l.rotulo }, l.rotulo),
          h("span", { class: "tr" }, h("span", { class: "fl", style: { width: Math.max(4, Math.round(Math.abs(l.valor) / max * 100)) + "%", background: Number(l.valor) < 0 ? COLORS[2] : COLORS[i < 3 ? 0 : 1] } })),
          h("span", { style: { fontWeight: 600, textAlign: "right" } }, nf(l.valor, dec) + (unit === "%" ? "%" : unit ? " " + unit : ""))))));
    }
    const chips = h("div", { class: "row wrap", style: { alignItems: "center", gap: "10px" } });
    const src = (m.sources || []).join(" · ") || (m.chart && m.chart.fonte);
    if (src) chips.appendChild(h("span", { class: "src" }, App.icon("db", 12), src));
    if (m.chart && m.chart.link && /^#\//.test(m.chart.link)) chips.appendChild(h("a", { class: "btn", href: m.chart.link, style: { minHeight: "36px" } }, "Ver na app →"));
    if (chips.childNodes.length) content.appendChild(chips);
    return h("div", { class: "msg-bot" }, h("div", { class: "bot-ic", "aria-hidden": "true" }, App.icon("spark", 18, "#E9A27C")), content);
  }

  App.pages.assistente = {
    title: "Assistente",
    async render() {
      const st = await api("agent/status");
      const ov = await api("overview?mode=" + (App.state.mode || "rent"));
      const log = h("div", { class: "chat-log", "aria-live": "polite" });
      const input = h("input", { type: "text", id: "pergunta", placeholder: "Pergunta sobre casas, zonas, carros ou a vossa shortlist…", autocomplete: "off" });
      const sendBtn = h("button", { type: "submit", class: "send", "aria-label": "Enviar pergunta" }, App.icon("send", 20, "#F6F2EA"));
      let busy = false;
      const draw = () => {
        clear(log);
        if (!messages.length) {
          log.appendChild(botMsg({ text: "Olá! Tenho acesso às medianas por zona (nossas e do INE), aos anúncios ativos com o histórico de preços, à vossa shortlist com votos e notas, e aos carros dos modelos que seguem.\n\nDigo sempre de onde vem cada número e quantos anúncios estão por trás dele. O que querem saber?" }));
        }
        messages.forEach((m) => log.appendChild(m.role === "user" ? h("div", { class: "msg-user" }, m.text) : botMsg(m)));
        if (busy) log.appendChild(h("div", { class: "muted small", style: { fontStyle: "italic" } }, "A consultar os dados…"));
        log.scrollTop = log.scrollHeight;
      };
      const ask = async (q) => {
        if (busy || !q.trim()) return;
        busy = true;
        messages.push({ role: "user", text: q });
        input.value = "";
        draw();
        try {
          const r = await api("agent", { body: { message: q, conversation } });
          conversation = r.conversation;
          messages.push({ role: "bot", text: r.answer, chart: r.chart, sources: r.sources });
        } catch (e) {
          messages.push({ role: "bot", text: "Não consegui responder: " + e.message });
        }
        busy = false;
        draw();
      };
      draw();
      const diary = st.diary;
      const insights = [];
      if (diary && diary.prose) insights.push({ tag: "Diário", color: "#9BC9C4", title: "Semana de " + App.dateShort(diary.week_start), text: diary.prose, q: "Resume o que mudou esta semana nas zonas que seguimos." });
      for (const e of ov.events.slice(0, 4)) insights.push({ tag: e.kind === "new" ? "Novos" : e.kind === "up" ? "A subir" : "A descer", color: e.kind === "down" ? "#E9A27C" : "#9BC9C4", title: e.texto, text: App.ago(e.when), q: "Explica-me isto: " + e.texto });

      const root = h("frag");
      add(root, h("div", { class: "with-filters" },
        h("section", { class: "card chat", "aria-label": "Conversa" },
          h("div", { class: "chat-head" },
            h("div", null, h("h1", null, "Pergunta aos vossos dados"), h("p", { class: "sub" }, "Responde só com o que está na base de dados e diz sempre de onde vem cada número")),
            h("button", { type: "button", class: "btn", onclick: () => { conversation = null; messages = []; draw(); } }, "Nova conversa")),
          log,
          h("div", { class: "chat-foot" },
            st.configured ? null : h("p", { class: "status-line warn" }, "O assistente ainda não está ligado a um modelo."),
            h("div", { class: "row wrap", role: "group", "aria-label": "Perguntas sugeridas", style: { gap: "8px" } },
              SUGGESTIONS.map((q) => h("button", { type: "button", class: "sug", style: { flex: "0 1 auto" }, onclick: () => ask(q) }, q))),
            h("form", { onsubmit: (e) => { e.preventDefault(); ask(input.value); } }, h("label", { class: "sr-only", for: "pergunta" }, "Pergunta"), input, sendBtn))),
        h("aside", { style: { flex: "1 1 340px", minWidth: 0, display: "flex", flexDirection: "column", gap: "20px" } },
          h("section", { class: "insights", "aria-label": "Insights da semana" },
            h("div", { class: "card-head", style: { alignItems: "baseline" } }, h("h2", null, "Insights da semana"), h("span", { class: "xsmall", style: { color: "var(--control)" } }, "a partir dos dados de hoje")),
            insights.length ? insights.map((i) => h("article", { class: "insight" }, h("span", { class: "tag", style: { color: i.color } }, i.tag),
              h("h3", { style: { fontSize: "16px", fontWeight: 600 } }, i.title), h("p", null, i.text),
              h("button", { type: "button", onclick: () => ask(i.q) }, "Perguntar mais"))) : h("p", { class: "small" }, "Ainda sem nada a assinalar.")),
          h("section", { class: "card tight", "aria-label": "O que o assistente consulta" },
            h("h2", { style: { fontSize: "20px" } }, "O que o assistente consulta"),
            h("ul", { class: "timeline" },
              h("li", null, h("span", null, "Anúncios e histórico de preços"), h("span", { class: "muted small" }, st.sources.listings ? App.ago(st.sources.listings) : "–")),
              h("li", null, h("span", null, "Medianas por zona (nossas)"), h("span", { class: "muted small" }, st.sources.aggregates ? App.dateShort(st.sources.aggregates) : "–")),
              h("li", null, h("span", null, "Medianas oficiais do INE"), h("span", { class: "muted small" }, st.sources.ine ? App.ago(st.sources.ine) : "–")),
              h("li", null, h("span", null, "Shortlist, votos e notas"), h("span", { class: "muted small" }, "em tempo real")),
              h("li", null, h("span", null, "Carros dos modelos seguidos"), h("span", { class: "muted small" }, st.sources.cars ? App.ago(st.sources.cars) : "–"))),
            h("p", { class: "xsmall muted", style: { margin: 0 } }, "Não inventa números: se um dado não existe, diz que não sabe. Gasto este mês: " + nf(st.spent_eur, 2) + " € de " + nf(st.budget_eur) + " €.")))));
      setTimeout(() => input.focus(), 0);
      return root;
    },
  };
})();
