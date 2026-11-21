/* Radar Web – pagina PAC (piano di accumulo su ETF). Usa le utilità di app.js (caricato dopo: le funzioni
   qui sotto vengono chiamate solo quando si apre la pagina). */
"use strict";

function euro(v, d = 2) { v = num(v); return v === null ? "–" : v.toLocaleString("it-IT", { minimumFractionDigits: d, maximumFractionDigits: d }) + " €"; }
function keur(v) { v = num(v); return v === null ? "–" : v >= 10000 ? Math.round(v / 1000).toLocaleString("it-IT") + " k€" : euro(v, 0); }

async function pagePac() {
  const d = await api("/api/pac");
  const t = d.totals, n = d.next, plan = d.plan;
  const hasBuys = d.buys.length > 0;
  const statusChip = { "fatto": '<span class="chip green">✅ fatto</span>', "da fare": '<span class="chip orange">⏰ da fare</span>', "in arrivo": '<span class="chip gray">🗓️ in arrivo</span>' }[n.status] || "";
  const missing = d.missing.length ? `<div class="card section"><p style="margin:0">⚠️ Prezzi non trovati per <b>${d.missing.map(esc).join(", ")}</b>: controlla il ticker (su Yahoo Finance: es. SWDA.MI per Borsa Italiana, VWCE.DE per Xetra).</p></div>` : "";

  const f = frag(`
    ${missing}
    <div class="grid kpis">
      ${kpi("💶 Valore del PAC", t.value == null ? "–" : euro(t.value), hasBuys ? `versati ${euro(t.cost)} in ${t.buys} acquisti` : "registra il primo acquisto qui sotto")}
      ${kpi("📈 Guadagno", t.gain == null ? "–" : (t.gain >= 0 ? "+" : "") + euro(t.gain), t.cost ? pct(t.gain / t.cost * 100) + " sul versato" : "", cls(t.gain))}
      ${kpi("🧾 Se vendessi oggi", t.net_if_sold == null ? "–" : euro(t.net_if_sold), t.tax_if_sold ? `tasse 26%: ${euro(t.tax_if_sold)}` : "nessuna tassa (non sei in guadagno)")}
      ${kpi("📉 Calo massimo", hasBuys ? pct(-t.max_drawdown, 1) : "–", "la discesa peggiore finora (senza contare i versamenti)", t.max_drawdown > 20 ? "neg" : "")}
      ${kpi("🏛️ Bollo annuo", t.stamp_year == null ? "–" : euro(t.stamp_year), "0,2% del valore, lo trattiene il broker")}
      ${kpi("📊 Rendimento", t.twr_pct == null ? "–" : pct(t.twr_pct), t.first ? `dal ${new Date(t.first).toLocaleDateString("it-IT")}, senza l'effetto dei versamenti` : "")}
    </div>

    <div class="grid cols-2 section">
      <div class="card"><h2>🗓️ Versamento di ${esc(new Date(n.month + "-01").toLocaleDateString("it-IT", { month: "long", year: "numeric" }))} ${statusChip}</h2>
        <p class="muted" style="margin-top:0">Giorno ${plan.day} · ${euro(n.amount)} · già versati questo mese ${euro(n.done_eur)}.
          Il versamento va sugli ETF sotto il peso obiettivo: così il piano si ribilancia <b>senza vendere</b> (e senza tasse).</p>
        <div id="pacNext"></div>
        <p class="muted" style="margin-bottom:0">Se il tuo broker non vende frazioni di quota, compra le quote intere; quello che avanza resta per il mese dopo.</p>
      </div>
      ${chartCard("💶 Valore e capitale versato", "cPac")}
    </div>

    <div class="grid cols-2 section">
      <div class="card"><h2>➕ Registra un acquisto</h2>
        <p class="muted" style="margin-top:0">Gli acquisti li fai tu dal broker: qui li segni per tenere il conto.</p>
        <form id="pacBuy" class="form pac-form">
          <label>Data<input type="date" name="date" value="${todayStr()}" max="${todayStr()}" required></label>
          <label>ETF<select name="ticker">${plan.etfs.map(e => `<option>${esc(e.ticker)}</option>`).join("")}</select></label>
          <label>Quote<input type="number" name="qty" step="any" min="0" required></label>
          <label>Prezzo per quota (€)<input type="number" name="price" step="any" min="0" required></label>
          <label>Commissioni (€)<input type="number" name="fees" step="any" min="0" value="${plan.fees || 0}"></label>
          <div class="row"><button class="btn primary">Salva acquisto</button></div>
        </form>
      </div>
      <div class="card"><h2>⚙️ Il tuo piano</h2>
        <form id="pacPlan" class="form pac-form">
          <label>Versamento mensile (€)<input type="number" name="monthly" step="any" min="1" value="${plan.monthly}" required></label>
          <label>Giorno del mese (1-28)<input type="number" name="day" min="1" max="28" value="${plan.day}" required></label>
          <label>Commissione tipica per acquisto (€)<input type="number" name="fees" step="any" min="0" value="${plan.fees || 0}"></label>
          <div id="pacEtfs"></div>
          <div class="row"><button type="button" class="btn" id="pacAdd">+ ETF</button><button class="btn primary">Salva piano</button></div>
        </form>
        <p class="muted">Esempi di ETF UCITS ad accumulazione (non consigli): ${d.examples.map(e => `<code title="${esc(e.name)}">${esc(e.ticker)}</code>`).join(" ")}. Il ticker è quello di Yahoo Finance.</p>
      </div>
    </div>

    <div class="card section"><h2>📦 Cosa possiedi</h2><div id="pacPos"></div></div>
    <div class="card section"><h2>🔭 Proiezione: ${euro(plan.monthly, 0)} al mese</h2>
      <p class="muted" style="margin-top:0">Rendimento annuo fisso ipotetico: 4% prudente, 7% storico medio di un ETF azionario globale, 10% ottimista. Nella realtà gli anni sono molto diversi tra loro (anche -30%). "Netto" = dopo il 26% sul guadagno, se vendi tutto alla fine.</p>
      <div id="pacProj"></div></div>
    <div class="card section"><h2>⏪ Prova sul passato: ${euro(plan.monthly, 0)} al mese su ${esc(d.backtest.ticker)}</h2>
      <p class="muted" style="margin-top:0">Un acquisto il primo giorno di borsa di ogni mese, ai prezzi veri. Mostra anche il momento peggiore: è lì che molti smettono, ed è l'errore più costoso.</p>
      <div id="pacBt"></div></div>
    <div class="card section"><h2>🧾 Acquisti registrati</h2><div id="pacBuys"></div></div>
    <div class="card section"><p class="muted" style="margin:0">ℹ️ Stime indicative, non consulenza finanziaria. Tasse: regime amministrato italiano, 26% sulla plusvalenza alla vendita (le minusvalenze degli ETF non compensano i guadagni degli ETF), bollo 0,2% l'anno; la parte in titoli di Stato degli ETF obbligazionari ha il 12,5% e qui è stimata prudenzialmente al 26%. Prezzi da Yahoo Finance, aggiornati ogni 6 ore${Object.keys(d.last_date).length ? " (ultimo: " + esc(Object.values(d.last_date).sort().pop()) + ")" : ""}.</p></div>`);

  // prossimo versamento
  $("#pacNext", f).appendChild(table(n.rows.map(r => ({ ...r })), [
    { key: "ticker", label: "ETF", fmt: (v, r) => `<b>${esc(v)}</b>${r.name ? `<br><small class="muted">${esc(r.name)}</small>` : ""}` },
    { key: "weight_now", label: "Peso ora", num: true, fmt: (v, r) => `${pct(v, 0, false)} <small class="muted">/ ${pct(r.target_weight, 0, false)}</small>` },
    { key: "eur", label: "Da versare", num: true, fmt: v => `<b>${euro(v)}</b>` },
    { key: "price", label: "Prezzo", num: true, fmt: v => v == null ? "–" : euro(v) },
    { key: "whole_qty", label: "Quote intere", num: true, fmt: (v, r) => v == null ? "–" : v === 0 ? `<span class="muted">0 (sotto 1 quota)</span>` : `${v} <small class="muted">(${(r.qty || 0).toFixed(2).replace(".", ",")})</small>` }]));

  // posizioni
  $("#pacPos", f).appendChild(d.positions.length ? table(d.positions, [
    { key: "ticker", label: "ETF", fmt: v => `<b>${esc(v)}</b>` },
    { key: "qty", label: "Quote", num: true, fmt: v => (+v).toLocaleString("it-IT", { maximumFractionDigits: 4 }) },
    { key: "avg_price", label: "Prezzo medio", num: true, fmt: v => euro(v) },
    { key: "price", label: "Prezzo ora", num: true, fmt: v => euro(v) },
    { key: "cost", label: "Versato", num: true, fmt: v => euro(v) },
    { key: "value", label: "Valore", num: true, fmt: v => `<b>${euro(v)}</b>` },
    { key: "gain", label: "Guadagno", num: true, fmt: (v, r) => `<span class="${cls(v)}">${v == null ? "–" : (v >= 0 ? "+" : "") + euro(v)} ${pct(r.gain_pct)}</span>` }]) : h(`<div>${empty("📦", "Ancora nessun acquisto")}</div>`));

  // proiezione
  $("#pacProj", f).appendChild(table(d.projection, [
    { key: "years", label: "Anni", num: true, fmt: v => `<b>${v}</b>` },
    { key: "invested", label: "Versato", num: true, fmt: v => keur(v) },
    { key: "r4", label: "4% (netto)", num: true, fmt: (v, r) => `${keur(v)} <small class="muted">(${keur(r.r4_net)})</small>` },
    { key: "r7", label: "7% (netto)", num: true, fmt: (v, r) => `<b>${keur(v)}</b> <small class="muted">(${keur(r.r7_net)})</small>` },
    { key: "r10", label: "10% (netto)", num: true, fmt: (v, r) => `${keur(v)} <small class="muted">(${keur(r.r10_net)})</small>` }]));

  // prova sul passato
  const bt = d.backtest.runs;
  $("#pacBt", f).appendChild(bt.length ? table(bt, [
    { key: "since", label: "Dal", fmt: (v, r) => `<b>${v}</b> <small class="muted">(${r.years} anni)</small>` },
    { key: "invested", label: "Versato", num: true, fmt: v => keur(v) },
    { key: "value", label: "Valore oggi", num: true, fmt: v => `<b>${keur(v)}</b>` },
    { key: "gain_pct", label: "Guadagno", num: true, fmt: v => `<span class="${cls(v)}">${pct(v, 0)}</span>` },
    { key: "etf_cagr", label: "ETF all'anno", num: true, fmt: v => pct(v) },
    { key: "etf_max_drawdown", label: "Calo max ETF", num: true, fmt: v => `<span class="neg">${pct(-v, 0)}</span>` },
    { key: "worst", label: "Momento peggiore", fmt: v => v && v.loss < 0 ? `${new Date(v.date).toLocaleDateString("it-IT", { month: "short", year: "numeric" })}: <span class="neg">${euro(v.loss, 0)}</span> su ${keur(v.invested)} versati` : "mai sotto il versato" }])
    : h(`<div>${empty("⏪", "Storia dei prezzi non disponibile per questo ETF")}</div>`));

  // acquisti registrati
  $("#pacBuys", f).appendChild(hasBuys ? table(d.buys, [
    { key: "date", label: "Data", fmt: v => new Date(v).toLocaleDateString("it-IT") },
    { key: "ticker", label: "ETF", fmt: v => `<b>${esc(v)}</b>` },
    { key: "qty", label: "Quote", num: true, fmt: v => (+v).toLocaleString("it-IT", { maximumFractionDigits: 4 }) },
    { key: "price", label: "Prezzo", num: true, fmt: v => euro(v) },
    { key: "fees", label: "Commissioni", num: true, fmt: v => euro(v) },
    { key: "id", label: "", sort: false, fmt: v => `<button class="btn small" data-del="${esc(v)}">🗑</button>` }]) : h(`<div>${empty("🧾", "Nessun acquisto registrato")}</div>`));
  $$("[data-del]", f).forEach(b => b.onclick = async e => {
    e.stopPropagation();
    if (!confirm("Eliminare questo acquisto?")) return;
    try { await api("/api/pac/buy/delete", { id: b.dataset.del }); render(true); } catch (er) { toast("Errore", er.message, "errore"); }
  });

  // modulo acquisto
  $("#pacBuy", f).onsubmit = async e => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    try { await api("/api/pac/buy", fd); toast("✅ Acquisto registrato", `${fd.qty} × ${fd.ticker}`); render(true); }
    catch (er) { toast("Acquisto non salvato", er.message, "errore"); }
  };

  // modulo piano con righe ETF
  const box = $("#pacEtfs", f);
  const addRow = (e = { ticker: "", weight: 0, name: "" }) => {
    const row = h(`<div class="row pac-etf"><input type="text" name="et" placeholder="Ticker (es. SWDA.MI)" value="${esc(e.ticker)}" style="flex:2" required>
      <input name="ew" type="number" step="any" min="0.01" max="100" placeholder="Peso %" value="${e.weight || ""}" style="flex:1" required>
      <input type="text" name="en" placeholder="Nome (facoltativo)" value="${esc(e.name)}" style="flex:3"><button type="button" class="btn small">✕</button></div>`);
    $("button", row).onclick = () => { if ($$(".pac-etf", box).length > 1) row.remove(); };
    box.appendChild(row);
  };
  plan.etfs.forEach(addRow);
  $("#pacAdd", f).onclick = () => addRow();
  $("#pacPlan", f).onsubmit = async e => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const etfs = $$(".pac-etf", box).map(r => ({ ticker: $("[name=et]", r).value, weight: $("[name=ew]", r).value, name: $("[name=en]", r).value }));
    try {
      await api("/api/pac/plan", { monthly: fd.get("monthly"), day: fd.get("day"), fees: fd.get("fees"), etfs });
      toast("✅ Piano salvato", "I pesi sono riportati a 100%."); render();
    } catch (er) { toast("Piano non salvato", er.message, "errore"); }
  };

  f.addEventListener("mounted", () => {
    const cv = $("#cPac"); if (!cv) return;
    if (!d.curve.length) { cv.parentElement.innerHTML = empty("📈", "Il grafico compare dopo il primo acquisto"); return; }
    chart(cv, { type: "line", legend: true, data: { labels: d.curve.map(p => p.t), datasets: [
      { label: "Valore", data: d.curve.map(p => p.v), borderColor: css("--green"), backgroundColor: css("--green"), pointRadius: 0, borderWidth: 2 },
      { label: "Versato", data: d.curve.map(p => p.inv), borderColor: css("--muted"), borderDash: [5, 4], pointRadius: 0, stepped: true }] },
      options: { scales: { x: { ticks: { maxTicksLimit: 8 } }, y: { ticks: { callback: v => v.toLocaleString("it-IT") + " €" } } } } });
  });
  return f;
}
