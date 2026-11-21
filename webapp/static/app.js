/* Radar Web – interfaccia (vanilla JS, nessuna build). */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const S = { overview: null, lastEvent: null, offset: 0, charts: [], unread: 0, page: null, lwChart: null };
const LS = {
  get(k, d) { try { const v = localStorage.getItem("radar." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("radar." + k, JSON.stringify(v)); } catch { /* storage non disponibile */ } },
};

/* ---------------- utilità ---------------- */
function esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); }
function num(v) { return v === null || v === undefined || Number.isNaN(+v) ? null : +v; }
function price(v) { v = num(v); if (v === null) return "–"; if (v >= 1000) return v.toLocaleString("it-IT", { maximumFractionDigits: 2 }); if (v >= 1) return v.toLocaleString("it-IT", { maximumFractionDigits: 4 }); return v.toPrecision(4); }
function pct(v, d = 1, sign = true) { v = num(v); return v === null ? "–" : (sign && v > 0 ? "+" : "") + v.toFixed(d).replace(".", ",") + "%"; }
function eur(v) { v = num(v); return v === null ? "–" : (v > 0 ? "+" : "") + v.toFixed(2).replace(".", ",") + " €"; }
function cls(v) { v = num(v); return v === null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : ""; }
function dt(iso, withDate = true) {
  if (!iso) return "–"; const d = new Date(iso); if (isNaN(d)) return iso;
  const t = d.toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" });
  return withDate ? d.toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit" }) + " " + t : t;
}
function hm(ms) { return ms ? new Date(ms).toLocaleTimeString("it-IT", { hour: "2-digit", minute: "2-digit" }) : "–"; }
function todayStr() { const d = new Date(); return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0"); }
function tfMs(tf) { const u = { m: 6e4, h: 36e5, d: 864e5, w: 6048e5 }; return (parseInt(tf) || 1) * u[tf.slice(-1)]; }
function h(html) { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; }

async function api(path, body) {
  const opt = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-Radar": "1" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || "HTTP " + r.status);
  return data;
}

const STATUS = {
  pending: ["⏳ In attesa BUY", "gray"], open: ["🔄 Aperta", "blue"], win: ["✅ SELL", "green"], loss: ["🛑 STOP", "red"],
  timeout: ["⏱️ Scaduta", "orange"], unfilled: ["❌ BUY non eseguito", "gray"],
  attesa: ["⏳ In attesa", "orange"], confermato: ["✅ Confermato", "green"], annullato: ["✖ Annullato", "red"], preavvisato: ["⏰→✅ Preavvisato", "green"],
};
function chip(key, fallback) { const s = STATUS[key]; return s ? `<span class="chip ${s[1]}">${s[0]}</span>` : (fallback ? `<span class="chip gray">${esc(fallback)}</span>` : ""); }
function kindChip(k) { return k === "massimo" ? '<span class="chip green">🟢 BUY massimo</span>' : k === "preavviso" ? '<span class="chip orange">⏰ Preavviso</span>' : `<span class="chip gray">${esc(k)}</span>`; }
function scoreChip(s) { s = num(s); if (s === null) return "–"; const c = s >= 50 ? "green" : s >= 20 ? "blue" : s <= -20 ? "red" : "gray"; return `<span class="chip ${c}">${s.toFixed(0)}</span>`; }
function spark(arr, w = 90, ht = 26) {
  if (!arr || arr.length < 2) return "";
  const mn = Math.min(...arr), mx = Math.max(...arr), r = mx - mn || 1;
  const pts = arr.map((v, i) => `${(i / (arr.length - 1) * w).toFixed(1)},${(ht - (v - mn) / r * ht).toFixed(1)}`).join(" ");
  const col = arr[arr.length - 1] >= arr[0] ? "var(--green)" : "var(--red)";
  return `<svg width="${w}" height="${ht}" viewBox="0 0 ${w} ${ht}"><polyline fill="none" stroke="${col}" stroke-width="1.6" points="${pts}"/></svg>`;
}
function empty(icon, text) { return `<div class="empty"><b>${icon}</b>${text}</div>`; }
function kpi(label, value, sub = "", klass = "") { return `<div class="card kpi"><small>${label}</small><b class="${klass}">${value}</b><span class="sub">${sub}</span></div>`; }

function toast(title, text = "", level = "info", ms = 7000) {
  const el = h(`<div class="toast ${level}"><b>${esc(title)}</b>${text ? `<p>${esc(text)}</p>` : ""}</div>`);
  $("#toasts").appendChild(el);
  el.onclick = () => el.remove();
  setTimeout(() => el.remove(), ms);
}

/* tabella ordinabile */
function table(rows, cols, onClick) {
  const wrap = h(`<div class="table-wrap"><table><thead><tr></tr></thead><tbody></tbody></table></div>`);
  const tr = $("thead tr", wrap), tb = $("tbody", wrap);
  let sortKey = null, dir = -1;
  cols.forEach(c => {
    const th = h(`<th class="${c.num ? "num" : ""}" ${c.sort !== false ? `data-sort="${c.key}"` : ""}>${c.label}</th>`);
    if (c.sort !== false) th.onclick = () => { dir = sortKey === c.key ? -dir : -1; sortKey = c.key; draw(); };
    tr.appendChild(th);
  });
  function draw() {
    let rs = rows.slice();
    if (sortKey) {
      const c = cols.find(x => x.key === sortKey), get = c.sortVal || (r => r[sortKey]);
      rs.sort((a, b) => { const x = get(a), y = get(b); if (x == null) return 1; if (y == null) return -1; return (x > y ? 1 : x < y ? -1 : 0) * dir; });
    }
    tb.innerHTML = rs.length ? "" : `<tr><td colspan="${cols.length}">${empty("∅", "Nessun dato")}</td></tr>`;
    rs.forEach(r => {
      const row = document.createElement("tr");
      if (onClick) { row.className = "click"; row.onclick = () => onClick(r); }
      row.innerHTML = cols.map(c => `<td class="${c.num ? "num" : ""}">${c.fmt ? c.fmt(r[c.key], r) : esc(r[c.key] ?? "–")}</td>`).join("");
      tb.appendChild(row);
    });
  }
  draw();
  return wrap;
}

/* grafici (Chart.js da CDN: se manca la rete i grafici non compaiono ma la pagina funziona) */
function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
function chart(canvas, cfg) {
  if (typeof Chart === "undefined") { canvas.parentElement.innerHTML = empty("📉", "Grafici non disponibili (libreria non caricata)"); return; }
  Chart.defaults.color = css("--muted"); Chart.defaults.borderColor = css("--line");
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  cfg.options = Object.assign({ responsive: true, maintainAspectRatio: false, plugins: { legend: { display: !!cfg.legend } } }, cfg.options || {});
  const c = new Chart(canvas, cfg); S.charts.push(c); return c;
}
function chartCard(title, id, extra = "") { return `<div class="card"><h2>${title}${extra}</h2><div class="chart-box"><canvas id="${id}"></canvas></div></div>`; }

/* ---------------- notifiche ---------------- */
let audioCtx = null;
function beep(kind) {
  if (!LS.get("sound", true)) return;
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    const notes = kind === "massimo" ? [660, 880, 1100] : kind === "preavviso" ? [520, 700] : [600];
    notes.forEach((f, i) => {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.frequency.value = f; o.type = "sine"; o.connect(g); g.connect(audioCtx.destination);
      const t = audioCtx.currentTime + i * 0.18;
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.25, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
      o.start(t); o.stop(t + 0.17);
    });
  } catch { /* audio non disponibile */ }
}
function browserNotify(ev) {
  if (!("Notification" in window) || Notification.permission !== "granted" || !LS.get("browserNotif", true)) return;
  try { const n = new Notification(ev.title, { body: ev.text.slice(0, 240), tag: "radar-" + ev.id }); n.onclick = () => { window.focus(); location.hash = "#/segnali"; }; } catch { /* */ }
}
const EV_ICON = { massimo: "🟢", preavviso: "⏰", errore: "⚠️", recap: "📋", info: "ℹ️", auto: "💼" };
function evHtml(e) { return `<div class="ev"><div class="ico">${EV_ICON[e.level] || "•"}</div><div><b>${esc(e.title)}</b> <time>${dt(e.ts)}</time><p>${esc(e.text)}</p></div></div>`; }

async function pollEvents() {
  try {
    if (S.lastEvent === null) { const d = await api("/api/events?after=0"); S.lastEvent = d.last; renderDrawer(d.events); return; }
    const d = await api("/api/events?after=" + S.lastEvent);
    if (!d.events.length) return;
    S.lastEvent = d.last;
    d.events.forEach(e => {
      if (["massimo", "preavviso", "errore", "recap", "auto"].includes(e.level) || e.title.includes("prova")) {
        toast(e.title, e.text, e.level, e.level === "massimo" ? 20000 : 9000);
        if (e.level === "massimo" || e.level === "preavviso") { beep(e.level); browserNotify(e); }
        else if (e.title.includes("prova")) { beep("info"); browserNotify(e); }
      }
    });
    S.unread += d.events.length; updateBell();
    prependDrawer(d.events);
    if (["dashboard", "segnali", "auto"].includes(S.page)) render(true);
  } catch { /* server momentaneamente non raggiungibile */ }
}
function updateBell() { const b = $("#bellCount"); b.textContent = S.unread; b.classList.toggle("hidden", !S.unread); document.title = (S.unread ? `(${S.unread}) ` : "") + "Radar Web"; }
function renderDrawer(evs) { $("#drawerBody").innerHTML = evs.length ? evs.slice().reverse().map(evHtml).join("") : empty("🔕", "Nessuna attività"); }
function prependDrawer(evs) { const b = $("#drawerBody"); if ($(".empty", b)) b.innerHTML = ""; evs.forEach(e => b.insertAdjacentHTML("afterbegin", evHtml(e))); }

/* ---------------- stato e conto alla rovescia ---------------- */
async function refreshOverview() {
  try {
    const o = await api("/api/overview");
    S.overview = o; S.offset = o.now - Date.now();
    const e = o.engine, el = $("#engineState");
    const state = !o.config.segnali_attivi ? ["warn", "Segnali disattivati"] : e.alive ? ["ok", `Motore attivo (${e.process === "bot" ? "nel bot" : "qui"})`] : ["bad", "Motore fermo"];
    el.innerHTML = `<i class="dot ${state[0]}"></i><span>${state[1]}</span>`;
    return o;
  } catch { $("#engineState").innerHTML = '<i class="dot bad"></i><span>Server non raggiungibile</span>'; }
}
function tickCountdown() {
  const o = S.overview; if (!o || !o.engine || !o.engine.next_close) return;
  const now = Date.now() + S.offset, tf = tfMs(o.config.segnali_timeframe || "1h");
  let close = o.engine.next_close; while (close <= now) close += tf;
  const lead = (o.engine.lead_min || o.config.preavviso_minuti || 20) * 6e4;
  let pre = close - lead; if (pre <= now && now < close) pre = null;
  const fmt = ms => { const s = Math.max(0, Math.round(ms / 1000)); const m = Math.floor(s / 60); return m >= 60 ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : `${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`; };
  $("#cdPre").textContent = pre === null ? "in corso" : fmt(pre - now);
  $("#cdClose").textContent = fmt(close - now) + " · " + hm(close);
  $("#countdown").classList.toggle("hot", pre === null);
}

/* ---------------- pagine ---------------- */
const PAGES = {
  dashboard: { title: "Dashboard", render: pageDashboard },
  segnali: { title: "Segnali BUY massimo e preavvisi", render: pageSignals },
  operazioni: { title: "Operazioni", render: pageOperations },
  auto: { title: "Investimento automatico", render: pageAuto },
  laboratorio: { title: "Laboratorio delle strategie", render: pageLab },
  pac: { title: "PAC – piano di accumulo su ETF", render: () => pagePac() },
  statistiche: { title: "Statistiche", render: pageStats },
  recap: { title: "Recap della giornata", render: pageRecap },
  calendario: { title: "Calendario degli errori", render: pageCalendar },
  radar: { title: "Radar live", render: pageRadar },
  bot: { title: "Bot Telegram", render: pageBot },
  impostazioni: { title: "Impostazioni", render: pageSettings },
};
function route() { const p = (location.hash.replace(/^#\/?/, "").split("?")[0]) || "dashboard"; return PAGES[p] ? p : "dashboard"; }
async function render(soft = false) {
  const p = route(); S.page = p;
  $$("#nav a").forEach(a => a.classList.toggle("active", a.dataset.page === p));
  $("#pageTitle").textContent = PAGES[p].title;
  $("#sidebar").classList.remove("open");
  const view = $("#view"), y = window.scrollY;
  if (!soft) view.innerHTML = empty("⏳", "Caricamento…");
  try {
    const node = await PAGES[p].render();
    if (S.page !== p) return;
    S.charts.forEach(c => c.destroy()); S.charts = [];
    view.innerHTML = ""; view.appendChild(node);
    node.dispatchEvent(new Event("mounted"));
    if (soft) window.scrollTo(0, y);
  } catch (e) { view.innerHTML = `<div class="card">${empty("⚠️", "Errore: " + esc(e.message))}</div>`; }
}
function frag(html) { const d = document.createElement("div"); d.innerHTML = html; return d; }

/* ---- mercato ---- */
function marketCard(m) {
  if (!m || !m.breadth) return "";
  const b = m.breadth, f = m.fear_greed, g = m.global, r = m.regime || {};
  const tone = r.level === "rialzo" ? "pos" : r.level === "ribasso" ? "neg" : "";
  const fmtB = v => v == null ? "–" : (v >= 1e12 ? (v / 1e12).toFixed(2).replace(".", ",") + " mila mld $" : (v / 1e9).toFixed(0) + " mld $");
  const fng = f ? `${f.value}/100 · ${esc(f.label)}${f.week_ago != null ? ` (7 giorni fa ${f.week_ago})` : ""}` : "–";
  return `<div class="card section"><h2>🌍 Mercato <span class="right muted" style="font-size:12px">${b.coins} coin seguite</span></h2>
    <p class="${tone}" style="margin:4px 0 10px">${esc(r.text || "")}</p>
    <div class="grid kpis">
      ${kpi("Coin sopra la media 200", `${b.above_ema200}/${b.coins}`, `${pct(b.above_pct, 0, false)} · in salita 24 h: ${b.rising_24h}`, b.above_pct >= 50 ? "pos" : "neg")}
      ${kpi("RSI medio", num(b.rsi_avg)?.toFixed(0) ?? "–", `${b.rsi_below_35} coin con RSI sotto 35`)}
      ${kpi("Variazione media 24 h", pct(b.change_24h_avg, 2), b.btc ? `BTC ${pct(b.btc.change_24h, 2)} · RSI ${num(b.btc.rsi)?.toFixed(0) ?? "–"}` : "", cls(b.change_24h_avg))}
      ${kpi("Paura / Avidità", f ? f.value : "–", fng, f && f.value <= 25 ? "neg" : f && f.value >= 75 ? "pos" : "")}
      ${kpi("Mercato crypto totale", fmtB(g && g.market_cap_usd), g ? `24 h ${pct(g.change_24h, 2)} · dominanza BTC ${pct(g.btc_dominance, 1, false)}` : "dati globali non ancora scaricati", g ? cls(g.change_24h) : "")}
    </div></div>`;
}

/* ---- dashboard ---- */
async function pageDashboard() {
  const o = await refreshOverview(); if (!o) throw new Error("server non raggiungibile");
  const t = o.today, tot = o.totals, c = o.config;
  const sigs = o.signals_today;
  const d = frag(`
    <div class="grid kpis">
      ${kpi(c.strategia === "rimbalzo" ? "🟢 Segnali rimbalzo oggi" : c.strategia === "dip" ? "🟢 Segnali compra il ribasso oggi" : "🟢 Segnali BUY massimo oggi", t.massimi, c.strategia === "rimbalzo" ? "crollo sotto la banda di Bollinger con BTC in salita" : c.strategia === "dip" ? `RSI sotto ${c.dip_rsi} con trend rialzista` : `soglia punteggio ${c.soglia_massimo}, prob. ≥ ${c.probabilita_minima}%`)}
      ${kpi("⏰ Preavvisi oggi", t.preavvisi, t.precisione_preavvisi == null ? `${t.in_attesa} in attesa` : `precisione ${pct(t.precisione_preavvisi, 0, false)} (${t.confermati}✓ ${t.annullati}✖)`)}
      ${kpi("🔄 Operazioni aperte", o.open_ops.length, `${t.ops_new} nuove oggi`)}
      ${kpi("💶 Risultato oggi", eur(t.net_eur), `${t.ops_closed} chiuse · ✅${t.wins} 🛑${t.losses}`, cls(t.net_eur))}
      ${kpi("🎯 Successo totale", tot.win_rate == null ? "–" : pct(tot.win_rate, 0, false), `${tot.closed || 0} operazioni chiuse · ${eur(tot.total_eur)}`)}
      ${kpi(`💼 <a href="#/auto">Investimento auto</a>`, o.auto.active ? (o.auto.mode === "reale" ? "ATTIVO 💶" : "ATTIVO 🧪") : "spento", `${o.auto.trades} acquisti oggi · ${o.auto.open} aperte · valore ${num(o.auto.value)?.toFixed(2) ?? "–"} ${(o.lab && o.lab.quote) || ""}`, o.auto.active ? "pos" : "")}
      ${kpi(`🧪 <a href="#/laboratorio">Laboratorio</a>`, o.lab && o.lab.summary ? money(o.lab.summary.pnl, o.lab.quote) : (o.lab && o.lab.running ? "⏳" : "–"), o.lab && o.lab.summary ? `${o.lab.summary.trades} operazioni rigiocate · ${pct(o.lab.summary.win_rate, 0, false)} vinte · ${o.lab.verified ? "✅ verificata" : "⚠️ non verificata"}` : "prova della strategia sui mesi passati", o.lab && o.lab.summary ? cls(o.lab.summary.pnl) : "")}
      ${kpi("⚠️ Errori oggi", (t.errors_pred || 0) + (t.errors_tech || 0), `${t.errors_pred} di previsione · ${t.errors_tech} tecnici`, (t.errors_pred + t.errors_tech) ? "neg" : "")}
    </div>
    ${marketCard(o.market)}
    <div class="grid cols-21 section">
      <div class="card"><h2>Segnali di oggi <a class="right" href="#/segnali">tutti →</a></h2><div id="sigCards"></div></div>
      <div class="card"><h2>📶 In avvicinamento <span class="right muted" style="font-size:12px">${o.watch.at ? "aggiornato " + dt(o.watch.at, false) : ""}</span></h2><div id="watch"></div></div>
    </div>
    <div class="grid cols-21 section">
      <div class="card"><h2>🛰️ Radar – ultima candela chiusa <span class="right muted" style="font-size:12px">${o.radar.at ? dt(o.radar.at) : "in attesa della prima scansione"}</span></h2><div id="radarT"></div></div>
      <div class="card"><h2>🔔 Attività recente</h2><div class="feed" id="feed"></div></div>
    </div>
    <div class="card section"><h2>🔄 Operazioni aperte <a class="right" href="#/operazioni">tutte →</a></h2><div id="openOps"></div></div>
    ${tot.verdict ? `<div class="verdict section">${esc(tot.verdict)}</div>` : ""}
  `);
  const sc = $("#sigCards", d);
  sc.innerHTML = sigs.length ? `<div class="sig-cards">${sigs.map(sigCard).join("")}</div>` :
    empty("🕐", `Nessun segnale oggi.<br>Il preavviso arriva ${c.preavviso_minuti} minuti prima della chiusura della candela ${esc(c.segnali_timeframe)}.`);
  $$(".sig", sc).forEach(el => el.onclick = () => openCoin(el.dataset.sym));
  const w = o.watch.rows || [];
  $("#watch", d).innerHTML = w.length ? w.map(r => watchRow(r, c)).join("") : empty("📭", "Nessuna coin vicina alla soglia.<br><small>Si aggiorna al momento del preavviso.</small>");
  $$("[data-sym]", $("#watch", d)).forEach(el => el.onclick = () => openCoin(el.dataset.sym));
  $("#radarT", d).appendChild(table(o.radar.rows, radarCols(true), r => openCoin(r.symbol)));
  $("#feed", d).innerHTML = o.events.length ? o.events.slice().reverse().slice(0, 12).map(evHtml).join("") : empty("🔕", "Nessuna attività");
  $("#openOps", d).appendChild(table(o.open_ops, opCols(true), r => openCoin(r.symbol)));
  return d;
}
function sigCard(s) {
  const pre = s.kind === "preavviso";
  return `<div class="sig ${pre ? "pre" : "max"}" data-sym="${esc(s.symbol)}">
    <div class="sig-head"><b>${esc(s.symbol.split("/")[0])}</b><small>${dt(s.created, false)}</small>${pre ? chip(s.status) : '<span class="chip green">🟢 BUY MASSIMO</span>'}</div>
    <div class="row" style="margin-top:6px"><span>Punteggio <b>${num(s.score)?.toFixed(0) ?? "–"}</b>${s.prev_score != null ? ` <small>(prima ${num(s.prev_score).toFixed(0)})</small>` : ""}</span><span class="spacer"></span><span>Prob. SELL <b>${pct(s.probability, 0, false)}</b></span></div>
    <div class="levels"><div><small>🟢 BUY</small><b>${price(s.buy)}</b></div><div><small>🎯 SELL ${pct(s.upside_pct)}</small><b class="pos">${price(s.sell)}</b></div><div><small>🛑 STOP -${num(s.risk_pct)?.toFixed(1) ?? "–"}%</small><b class="neg">${price(s.stop)}</b></div></div>
    ${pre && s.details && s.details.minutes ? `<small>Annunciato ${s.details.minutes} min prima della chiusura (${hm(s.candle_close)})</small>` : ""}
  </div>`;
}
function watchRow(r, c) {
  const s = Math.max(0, Math.min(100, r.score || 0));
  return `<div data-sym="${esc(r.symbol)}" style="cursor:pointer;padding:8px 0;border-bottom:1px solid var(--line)">
    <div class="row"><b>${esc(r.symbol)}</b><span class="spacer"></span><small>provvisorio</small> ${scoreChip(r.score)} <small>prima ${r.prev_score == null ? "–" : r.prev_score.toFixed(0)}</small></div>
    <div class="meter"><i style="width:${s}%"></i><u style="left:${c.soglia_massimo}%"></u></div>
    <small>prob. SELL ${pct(r.probability, 0, false)} · RSI ${num(r.rsi)?.toFixed(0) ?? "–"}${r.alerts ? " · " + esc(r.alerts) : ""}</small></div>`;
}
function radarCols(short) {
  const cols = [
    { key: "symbol", label: "Coin", fmt: v => `<b>${esc(v)}</b>` },
    { key: "price", label: "Prezzo", num: true, fmt: price },
    { key: "change_24h", label: "24h", num: true, fmt: v => `<span class="${cls(v)}">${pct(v)}</span>` },
    { key: "score", label: "Punteggio", num: true, fmt: scoreChip },
    { key: "probability", label: "Prob. SELL", num: true, fmt: v => pct(v, 0, false) },
    { key: "spark", label: "Andamento", sort: false, fmt: v => spark(v) },
  ];
  if (!short) cols.splice(5, 0,
    { key: "rsi", label: "RSI", num: true, fmt: v => num(v)?.toFixed(0) ?? "–" },
    { key: "vol_ratio", label: "Volume", num: true, fmt: v => num(v) ? "x" + v.toFixed(1) : "–" },
    { key: "buy", label: "BUY", num: true, fmt: price },
    { key: "sell", label: "SELL", num: true, fmt: price },
    { key: "stop", label: "STOP", num: true, fmt: price },
    { key: "bt_win_rate", label: "Storico", num: true, fmt: (v, r) => v == null ? "–" : `${pct(v, 0, false)} <small>(${r.bt_trades})</small>` },
    { key: "alerts", label: "Allerte", sort: false, fmt: v => `<small>${esc(v || "")}</small>` });
  return cols;
}
function opCols(short) {
  const cols = [
    { key: "created", label: "Segnale", fmt: v => dt(v) },
    { key: "symbol", label: "Coin", fmt: v => `<b>${esc(v)}</b>` },
    { key: "status", label: "Stato", fmt: v => chip(v, v) },
    { key: "probability", label: "Prob.", num: true, fmt: v => pct(v * 100, 0, false) },
    { key: "buy", label: "BUY", num: true, fmt: price },
    { key: "sell", label: "SELL", num: true, fmt: price },
    { key: "stop", label: "STOP", num: true, fmt: price },
    { key: "last_price", label: "Ultimo", num: true, fmt: price },
    { key: "ret_pct", label: "Risultato", num: true, sortVal: r => r.ret_pct ?? r.unreal_pct, fmt: (v, r) => v != null ? `<b class="${cls(v)}">${pct(v, 2)}</b>` : r.unreal_pct != null ? `<span class="${cls(r.unreal_pct)}">${pct(r.unreal_pct, 2)}</span> <small>aperta</small>` : "–" },
  ];
  if (!short) cols.push(
    { key: "net_pct", label: "Netto", num: true, fmt: v => `<span class="${cls(v)}">${pct(v, 2)}</span>` },
    { key: "net_eur", label: "€ (100€)", num: true, sortVal: r => r.net_pct, fmt: (v, r) => r.net_pct == null ? "–" : `<span class="${cls(r.net_pct)}">${eur(r.net_pct)}</span>` },
    { key: "hours", label: "Durata", num: true, fmt: v => v == null ? "–" : v.toFixed(0) + " h" },
    { key: "mfe_pct", label: "Max ↑", num: true, fmt: v => pct(v, 1) },
    { key: "mae_pct", label: "Max ↓", num: true, fmt: v => pct(v, 1) },
    { key: "closed_at", label: "Chiusa", fmt: v => dt(v) });
  return cols;
}

/* ---- segnali ---- */
async function pageSignals() {
  const days = LS.get("sig.days", 7), kind = LS.get("sig.kind", "");
  const d = await api(`/api/signals?days=${days}&kind=${kind}`);
  const list = d.signals;
  const pre = list.filter(s => s.kind === "preavviso"), conf = pre.filter(s => s.status === "confermato").length, canc = pre.filter(s => s.status === "annullato").length;
  const f = frag(`
    <div class="row">
      <div class="seg" id="days">${[1, 7, 30, 90].map(x => `<button data-v="${x}" class="${x === days ? "on" : ""}">${x === 1 ? "Oggi" : x + " giorni"}</button>`).join("")}</div>
      <div class="seg" id="kind">${[["", "Tutti"], ["massimo", "🟢 Massimo"], ["preavviso", "⏰ Preavvisi"]].map(([v, l]) => `<button data-v="${v}" class="${v === kind ? "on" : ""}">${l}</button>`).join("")}</div>
      <input type="text" id="q" placeholder="Cerca coin…" style="max-width:200px">
      <span class="spacer"></span><a class="btn" href="/api/export/segnali.csv">⬇ CSV</a>
    </div>
    <div class="grid kpis section">
      ${kpi("Segnali BUY massimo", list.filter(s => s.kind === "massimo").length)}
      ${kpi("Preavvisi", pre.length)}
      ${kpi("Preavvisi confermati", conf, canc + conf ? "precisione " + pct(conf / (conf + canc) * 100, 0, false) : "")}
      ${kpi("Preavvisi annullati", canc, "contano come errori di previsione", canc ? "neg" : "")}
    </div>
    <div class="card section"><div id="t"></div></div>
    <div class="card section"><h2>Come funziona</h2><p class="muted" style="margin:0">⏰ <b>Preavviso</b>: qualche minuto prima della chiusura della candela il punteggio viene calcolato sulla candela ancora aperta; se supera la soglia di preavviso arriva l'avviso “potrebbe scattare”.<br>🟢 <b>BUY massimo</b>: alla chiusura della candela il punteggio definitivo supera la soglia massima e la probabilità di arrivare al SELL prima dello STOP è sopra il minimo. Ogni preavviso viene poi segnato ✅ confermato o ✖ annullato.</p></div>`);
  const cols = [
    { key: "created", label: "Quando", fmt: v => dt(v) },
    { key: "kind", label: "Tipo", fmt: kindChip },
    { key: "status", label: "Esito", fmt: (v, r) => r.kind === "preavviso" || v ? chip(v) : "" },
    { key: "symbol", label: "Coin", fmt: v => `<b>${esc(v)}</b>` },
    { key: "candle_close", label: "Candela", fmt: (v, r) => `${esc(r.timeframe)} ${hm(v)}` },
    { key: "score", label: "Punteggio", num: true, fmt: (v, r) => `${r.prev_score != null ? `<small>${r.prev_score.toFixed(0)} →</small> ` : ""}${scoreChip(v)}` },
    { key: "probability", label: "Prob. SELL", num: true, fmt: v => pct(v, 0, false) },
    { key: "price", label: "Prezzo", num: true, fmt: price },
    { key: "buy", label: "BUY", num: true, fmt: price },
    { key: "sell", label: "SELL", num: true, fmt: v => `<span class="pos">${price(v)}</span>` },
    { key: "stop", label: "STOP", num: true, fmt: v => `<span class="neg">${price(v)}</span>` },
  ];
  const holder = $("#t", f);
  const draw = q => { holder.innerHTML = ""; holder.appendChild(table(list.filter(s => !q || s.symbol.toLowerCase().includes(q.toLowerCase())), cols, r => openCoin(r.symbol))); };
  draw("");
  $("#q", f).oninput = e => draw(e.target.value);
  $$("#days button", f).forEach(b => b.onclick = () => { LS.set("sig.days", +b.dataset.v); render(); });
  $$("#kind button", f).forEach(b => b.onclick = () => { LS.set("sig.kind", b.dataset.v); render(); });
  return f;
}

/* ---- operazioni ---- */
async function pageOperations() {
  const src = LS.get("ops.src", "massimi"), filt = LS.get("ops.filter", "");
  const d = await api("/api/operations?source=" + src);
  const s = d.summary;
  let ops = d.operations; if (filt) ops = ops.filter(o => filt === "closed" ? ["win", "loss", "timeout"].includes(o.status) : o.status === filt);
  const f = frag(`
    <div class="row">
      <div class="seg" id="src"><button data-v="massimi" class="${src === "massimi" ? "on" : ""}">🟢 Segnali BUY massimo</button><button data-v="test" class="${src === "test" ? "on" : ""}">🧪 Test previsioni (tutti i segnali)</button></div>
      <select id="filt" style="max-width:200px">${[["", "Tutti gli stati"], ["open", "🔄 Aperte"], ["pending", "⏳ In attesa BUY"], ["closed", "Chiuse"], ["win", "✅ SELL"], ["loss", "🛑 STOP"], ["timeout", "⏱️ Scadute"], ["unfilled", "❌ Non eseguite"]].map(([v, l]) => `<option value="${v}" ${v === filt ? "selected" : ""}>${l}</option>`).join("")}</select>
      <span class="spacer"></span><a class="btn" href="/api/export/${src === "massimi" ? "operazioni" : "test"}.csv">⬇ CSV</a>
    </div>
    <div class="grid kpis section">
      ${kpi("Operazioni", s.total || 0, `${s.open || 0} aperte · ${s.pending || 0} in attesa`)}
      ${kpi("Chiuse", s.closed || 0, `✅ ${s.wins || 0} · 🛑 ${s.losses || 0} · ⏱️ ${s.timeouts || 0}`)}
      ${kpi("Successo reale", s.win_rate == null ? "–" : pct(s.win_rate, 0, false), s.prob_mean == null ? "" : `ipotizzato ${pct(s.prob_mean, 0, false)}`)}
      ${kpi("Risultato (100€/op.)", eur(s.total_eur), s.net_mean == null ? "" : `medio ${pct(s.net_mean, 2)} netto`, cls(s.total_eur))}
      ${kpi("Profit factor", s.profit_factor == null ? "–" : s.profit_factor.toFixed(2).replace(".", ","), "guadagni / perdite")}
      ${kpi("Drawdown massimo", eur(s.max_drawdown_eur), s.fill_rate == null ? "" : `BUY eseguiti ${pct(s.fill_rate, 0, false)}`, "neg")}
    </div>
    ${s.verdict ? `<div class="verdict section">${esc(s.verdict)}</div>` : ""}
    <div class="card section"><div id="t"></div></div>`);
  $("#t", f).appendChild(table(ops, opCols(false), r => openCoin(r.symbol)));
  $$("#src button", f).forEach(b => b.onclick = () => { LS.set("ops.src", b.dataset.v); render(); });
  $("#filt", f).onchange = e => { LS.set("ops.filter", e.target.value); render(); };
  return f;
}

/* ---- statistiche ---- */
async function pageStats() {
  const src = LS.get("stats.src", "massimi");
  const d = await api("/api/stats?source=" + src), s = d.summary;
  const f = frag(`
    <div class="row"><div class="seg" id="src"><button data-v="massimi" class="${src === "massimi" ? "on" : ""}">🟢 Segnali BUY massimo</button><button data-v="test" class="${src === "test" ? "on" : ""}">🧪 Test previsioni</button></div></div>
    <div class="grid kpis section">
      ${kpi("Successo reale", s.win_rate == null ? "–" : pct(s.win_rate, 0, false), s.prob_mean == null ? "" : `probabilità ipotizzata ${pct(s.prob_mean, 0, false)}`)}
      ${kpi("Risultato totale", eur(s.total_eur), `${s.closed || 0} operazioni da ${d.stake}€`, cls(s.total_eur))}
      ${kpi("Precisione preavvisi", d.prealert_precision == null ? "–" : pct(d.prealert_precision, 0, false), `${d.prealert_counts.confermati} confermati · ${d.prealert_counts.annullati} annullati (30 gg)`)}
      ${kpi("Brier score", s.brier == null ? "–" : s.brier.toFixed(3).replace(".", ","), "più basso = probabilità più affidabili")}
      ${kpi("Durata media", s.hours_mean == null ? "–" : s.hours_mean.toFixed(0) + " h", s.mfe_mean == null ? "" : `max ↑ ${pct(s.mfe_mean)} · max ↓ ${pct(s.mae_mean)}`)}
      ${kpi("Migliore / peggiore", s.best ? `<span class="pos">${esc(s.best[0].split("/")[0])}</span> / <span class="neg">${esc(s.worst[0].split("/")[0])}</span>` : "–", s.best ? `${pct(s.best[1], 2)} / ${pct(s.worst[1], 2)}` : "")}
    </div>
    <div class="grid cols-2 section">
      ${chartCard("💶 Andamento del capitale (100€ per operazione)", "cEq")}
      ${chartCard("🟢 Segnali e preavvisi per giorno (30 gg)", "cSig")}
      ${chartCard("🎯 Calibrazione: probabilità ipotizzata vs successo reale", "cCal")}
      ${chartCard("📊 Distribuzione dei risultati netti", "cDist")}
      ${chartCard("🕐 Successo per ora del segnale", "cHour")}
      ${chartCard("📆 Risultato per giorno della settimana", "cWd")}
    </div>
    <div class="card section"><h2>🪙 Risultati per coin</h2><div id="coins"></div></div>`);
  $$("#src button", f).forEach(b => b.onclick = () => { LS.set("stats.src", b.dataset.v); render(); });
  $("#coins", f).appendChild(table(d.per_coin, [
    { key: "symbol", label: "Coin", fmt: v => `<b>${esc(v)}</b>` }, { key: "n", label: "Operazioni", num: true },
    { key: "wins", label: "✅ SELL", num: true }, { key: "losses", label: "🛑 STOP", num: true },
    { key: "win_rate", label: "Successo", num: true, fmt: v => pct(v, 0, false) },
    { key: "net_eur", label: "Risultato", num: true, fmt: v => `<b class="${cls(v)}">${eur(v)}</b>` }], r => openCoin(r.symbol)));
  f.addEventListener("mounted", () => {
    const G = css("--green"), R = css("--red"), O = css("--orange"), A = css("--accent");
    if (d.equity.length) chart($("#cEq"), { type: "line", data: { labels: d.equity.map(e => dt(e.t)), datasets: [{ data: d.equity.map(e => e.eur), borderColor: A, backgroundColor: A + "22", fill: true, tension: .25, pointRadius: 2 }] } });
    else $("#cEq").parentElement.innerHTML = empty("📈", "Nessuna operazione chiusa finora");
    const spd = d.signals_per_day;
    if (spd.length) chart($("#cSig"), { type: "bar", legend: true, data: { labels: spd.map(x => x.day.slice(5)), datasets: [
      { label: "BUY massimo", data: spd.map(x => x.massimi), backgroundColor: G },
      { label: "Preavvisi confermati", data: spd.map(x => x.confermati), backgroundColor: O },
      { label: "Preavvisi annullati", data: spd.map(x => x.annullati), backgroundColor: R }] }, options: { scales: { x: { stacked: false } } } });
    else $("#cSig").parentElement.innerHTML = empty("🟢", "Nessun segnale negli ultimi 30 giorni");
    if (d.calibration.length) chart($("#cCal"), { type: "line", legend: true, data: { labels: d.calibration.map(c => `${c.bucket} (${c.n})`), datasets: [
      { label: "Ipotizzata", data: d.calibration.map(c => c.predicted), borderColor: css("--muted"), borderDash: [5, 4] },
      { label: "Reale", data: d.calibration.map(c => c.actual), borderColor: G, backgroundColor: G }] }, options: { scales: { y: { min: 0, max: 100, ticks: { callback: v => v + "%" } } } } });
    else $("#cCal").parentElement.innerHTML = empty("🎯", "Servono operazioni chiuse");
    if (d.distribution.length) chart($("#cDist"), { type: "bar", data: { labels: d.distribution.map(x => x.bin), datasets: [{ data: d.distribution.map(x => x.n), backgroundColor: d.distribution.map(x => x.bin.startsWith("-") || x.bin.startsWith("<") ? R : G) }] } });
    else $("#cDist").parentElement.innerHTML = empty("📊", "Servono operazioni chiuse");
    const bh = d.by_hour.filter(x => x.n);
    if (bh.length) chart($("#cHour"), { type: "bar", data: { labels: d.by_hour.map(x => x.hour + ":00"), datasets: [{ data: d.by_hour.map(x => x.win_rate), backgroundColor: A }] }, options: { plugins: { tooltip: { callbacks: { label: c => `${(c.raw ?? 0).toFixed(0)}% su ${d.by_hour[c.dataIndex].n} op.` } } }, scales: { y: { min: 0, max: 100 } } } });
    else $("#cHour").parentElement.innerHTML = empty("🕐", "Servono operazioni chiuse");
    if (d.by_weekday.some(x => x.n)) chart($("#cWd"), { type: "bar", data: { labels: d.by_weekday.map(x => x.day), datasets: [{ data: d.by_weekday.map(x => x.net_eur), backgroundColor: d.by_weekday.map(x => x.net_eur >= 0 ? G : R) }] } });
    else $("#cWd").parentElement.innerHTML = empty("📆", "Servono operazioni chiuse");
  });
  return f;
}

/* ---- laboratorio delle strategie ---- */
function stratName(id) {
  if (!id) return "–";
  if (id.startsWith("rimb|")) {
    const [, tf, band, ks, hz] = id.split("|");
    return `🔬 Rimbalzo · candele ${tf} · sotto Bollinger ${band.replace(".", ",")}σ con BTC in salita · STOP ${ks.replace(".", ",")} ATR · SELL alla media · max ${hz} candele`;
  }
  if (id.startsWith("dip|")) {
    const [, tf, rsi, ks, kt] = id.split("|");
    return `Compra il ribasso · candele ${tf} · RSI < ${rsi} · STOP ${ks.replace(".", ",")} ATR · SELL ${kt.replace(".", ",")} ATR`;
  }
  const [tf, th, pm] = id.split("|");
  return `Candele ${tf} · punteggio ≥ ${th} · probabilità ≥ ${pm}%`;
}
function money(v, q) { v = num(v); return v === null ? "–" : (v > 0 ? "+" : "") + v.toFixed(2).replace(".", ",") + " " + (q || ""); }
async function pageLab() {
  const d = await api("/api/lab");
  const q = d.quote || "USDC", det = d.detail || null, s = det ? det.summary : null;
  if (!d.generated_at) {
    if (d.running) setTimeout(() => { if (S.page === "laboratorio") render(true); }, 10000);
    const f0 = frag(`<div class="card">${empty(d.running ? "⏳" : "🧪", d.running ?
      "Il laboratorio sta rigiocando la strategia sui prezzi degli ultimi mesi.<br>Ci vuole circa 1-2 minuti: la pagina si aggiorna da sola." :
      "Nessun risultato ancora.")}</div>${d.running ? "" : `<div class="row section"><button class="btn primary" id="labRun">▶ Avvia il laboratorio</button></div>`}`);
    const b = $("#labRun", f0); if (b) b.onclick = async () => { await api("/api/lab/run", {}); render(); };
    return f0;
  }
  const cur = (d.rows || []).find(r => r.id === d.current);
  const verifiedTxt = d.verified
    ? `<span class="chip green">✅ Strategia verificata</span> La migliore del periodo di scelta guadagna anche nel periodo di verifica.`
    : `<span class="chip orange">⚠️ Nessuna strategia verificata</span> Nessuna combinazione guadagna sia nel periodo di scelta sia in quello di verifica: il Laboratorio <b>non cambia</b> la strategia in uso e continua a controllare ogni ${d.refresh_hours} ore.`;
  const cutDay = cur && cur.cut ? new Date(cur.cut).toLocaleDateString("it-IT") : "";
  const f = frag(`
    <div class="card section"><p style="margin:0">${verifiedTxt}</p>
      <p class="muted" style="margin:8px 0 0">Ultimo aggiornamento ${dt(d.generated_at)} · ${esc(d.exchange)} · ${d.coins} coin in ${esc(q)} · commissione ${String(d.fee_pct).replace(".", ",")}% per lato · si rifà da solo ogni ${d.refresh_hours} ore${d.running ? " · ⏳ <b>aggiornamento in corso…</b>" : ""}</p>
      ${d.applied ? `<p style="margin:8px 0 0">🔁 Applicata ora: <b>${esc(stratName(d.applied))}</b></p>` : ""}
    </div>
    ${d.watch ? `<div class="card section"><h2>🔬 In osservazione: ${esc(stratName(d.watch.id))}</h2>
      <p style="margin:0">${d.watch.verified ? '<span class="chip green">✅ guadagna anche nel periodo di verifica</span>' : '<span class="chip orange">⚠️ non ancora verificata</span>'}
      Anno: <b class="${cls(d.watch.full.pnl)}">${money(d.watch.full.pnl, q)}</b> su ${d.start_capital} · ${d.watch.full.trades} operazioni · PF ${String(d.watch.full.profit_factor ?? "–").replace(".", ",")} · calo massimo ${pct(-d.watch.full.max_drawdown, 1)} ·
      verifica: <b class="${cls(d.watch.check.pnl)}">${money(d.watch.check.pnl, q)}</b> in ${d.watch.check.trades} operazioni</p>
      <p class="muted" style="margin:8px 0 0">Nuova strategia trovata nella ricerca: compra le coin crollate sotto la banda di Bollinger solo quando BTC è in salita, e rivende quando il prezzo torna alla media. Il vantaggio nei test è piccolo e sensibile alle commissioni: il Laboratorio <b>non la applica mai da solo</b> e la ricontrolla ogni ${d.refresh_hours} ore sui dati nuovi. Si sceglie a mano in Impostazioni (strategia = rimbalzo), solo in simulazione finché non si dimostra sui dati nuovi.</p></div>` : ""}
    <h2 class="section">Strategia in uso: ${esc(stratName(det ? det.id : d.current))}</h2>
    <div class="grid kpis">
      ${kpi("Operazioni rigiocate", s ? s.trades : "–", s ? `circa ${String(s.per_week).replace(".", ",")} a settimana in ${Math.round(s.days)} giorni` : "")}
      ${kpi("Risultato", s ? money(s.pnl, q) : "–", s ? `partendo da ${d.start_capital} ${esc(q)} (${pct(s.return_pct)})` : "", s ? cls(s.pnl) : "")}
      ${kpi("Operazioni vinte", s && s.win_rate != null ? pct(s.win_rate, 0, false) : "–", s ? `✅ ${s.wins} SELL · 🛑 ${s.losses} STOP` : "")}
      ${kpi("Calo massimo", s ? pct(-s.max_drawdown, 1) : "–", "la discesa peggiore del capitale", s && s.max_drawdown > 15 ? "neg" : "")}
      ${kpi("Profit factor", s && s.profit_factor != null ? String(s.profit_factor).replace(".", ",") : "–", "guadagni ÷ perdite: sopra 1 = in guadagno", s ? (s.profit_factor > 1 ? "pos" : "neg") : "")}
      ${kpi("Nel periodo di verifica", cur ? money(cur.check.pnl, q) : "–", cur ? `${cur.check.trades} operazioni dal ${cutDay}` : "", cur ? cls(cur.check.pnl) : "")}
    </div>
    <div class="grid cols-2 section">
      ${chartCard(`💶 Capitale della strategia in uso (da ${d.start_capital} ${esc(q)})`, "cLab")}
      <div class="card"><h2>⚙️ Come funziona</h2>
        <p class="muted" style="margin-top:0">Il laboratorio rigioca <b>le stesse regole del bot</b> (BUY limite valido 5 candele, poi SELL, STOP o scadenza, commissioni e slittamento allo STOP) su ogni candela degli ultimi mesi, usando solo i dati disponibili in quel momento.</p>
        <p class="muted">Prova ${(d.rows || []).length} combinazioni: le sceglie sui primi 2/3 del periodo e le <b>verifica</b> sull'ultimo terzo, che non ha usato per scegliere. Una strategia viene applicata solo se guadagna in tutti e due.</p>
        <p class="muted">Più operazioni non vuol dire più guadagno: nei test, abbassare le soglie fa comprare di più ma perdere di più.</p>
        <div class="row"><label><input type="checkbox" id="labAuto" ${d.auto_apply ? "checked" : ""}> Applica da sola la strategia migliore verificata</label>
        <button class="btn" id="labRun" ${d.running ? "disabled" : ""}>⟳ Rifai ora</button></div>
      </div>
    </div>
    <div class="card section"><h2>🧪 Tutte le strategie provate</h2><div id="labRows"></div></div>
    <div class="card section"><h2>📜 Operazioni rigiocate della strategia in uso <span class="right muted" style="font-size:12px">più recenti in alto</span></h2><div id="labTrades"></div></div>`);
  $("#labRun", f).onclick = async () => { await api("/api/lab/run", {}); toast("🧪 Laboratorio avviato", "Circa 1-2 minuti: la pagina si aggiorna da sola."); render(); };
  $("#labAuto", f).onchange = async e => { await api("/api/lab/auto", { active: e.target.checked }); toast(e.target.checked ? "Applicazione automatica attivata" : "Applicazione automatica disattivata"); };
  const rows = (d.rows || []).map(r => ({ ...r, name: r.id, cp: r.choice.pnl, ct: r.choice.trades, vp: r.check.pnl, vt: r.check.trades,
    vw: r.check.win_rate, vpf: r.check.profit_factor, wk: r.full.per_week }));
  $("#labRows", f).appendChild(table(rows, [
    { key: "name", label: "Strategia", fmt: (v, r) => `${esc(stratName(v))} ${r.id === d.current ? '<span class="chip blue">in uso</span>' : ""}${r.chosen ? ' <span class="chip gray">migliore nella scelta</span>' : ""}` },
    { key: "wk", label: "Op./settimana", num: true, fmt: v => String(v).replace(".", ",") },
    { key: "ct", label: "Scelta: op.", num: true },
    { key: "cp", label: "Scelta: risultato", num: true, fmt: v => `<span class="${cls(v)}">${money(v, q)}</span>` },
    { key: "vt", label: "Verifica: op.", num: true },
    { key: "vp", label: "Verifica: risultato", num: true, fmt: v => `<b class="${cls(v)}">${money(v, q)}</b>` },
    { key: "vw", label: "Verifica: vinte", num: true, fmt: v => v == null ? "–" : pct(v, 0, false) },
    { key: "vpf", label: "Verifica: PF", num: true, fmt: v => v == null ? "–" : String(v).replace(".", ",") }]));
  const trades = det ? det.trades.slice().reverse().map(t => ({ ...t, when: new Date(t.t).toISOString() })) : [];
  $("#labTrades", f).appendChild(table(trades, [
    { key: "when", label: "Segnale", fmt: v => dt(v) },
    { key: "symbol", label: "Coin", fmt: v => `<b>${esc(v)}</b>` },
    { key: "entry", label: "Comprato a", num: true, fmt: v => price(v) },
    { key: "exit", label: "Venduto a", num: true, fmt: v => price(v) },
    { key: "status", label: "Esito", fmt: v => chip(v, v) },
    { key: "ret", label: "Netto", num: true, fmt: v => `<span class="${cls(v)}">${pct(v, 2)}</span>` },
    { key: "pnl", label: "Risultato", num: true, fmt: v => `<b class="${cls(v)}">${money(v, q)}</b>` }], r => openCoin(r.symbol)));
  if (d.running) setTimeout(() => { if (S.page === "laboratorio") render(true); }, 15000);
  f.addEventListener("mounted", () => {
    const cv = $("#cLab"); if (!cv) return;
    const pts = det ? det.curve : [];
    if (!pts.length) { cv.parentElement.innerHTML = empty("📈", "Nessuna operazione chiusa per questa strategia"); return; }
    const data = [{ t: pts[0].t, v: d.start_capital }, ...pts];
    const last = data[data.length - 1].v, col = last >= d.start_capital ? css("--green") : css("--red");
    chart(cv, { type: "line", data: { labels: data.map(p => new Date(p.t).toLocaleDateString("it-IT", { day: "2-digit", month: "2-digit" })),
      datasets: [{ data: data.map(p => p.v), borderColor: col, backgroundColor: col + "22", fill: true, borderWidth: 2, tension: .2, pointRadius: 0, pointHitRadius: 10 }] },
      options: { interaction: { mode: "index", intersect: false }, plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => `Capitale ${c.raw.toFixed(2).replace(".", ",")} ${q}` } } },
        scales: { y: { ticks: { callback: v => v + " " + q } } } } });
  });
  return f;
}

/* ---- recap ---- */
async function pageRecap() {
  const day = LS.get("recap.day", todayStr()) || todayStr();
  const r = await api("/api/recap?day=" + day);
  const shift = n => { const x = new Date(day + "T12:00:00"); x.setDate(x.getDate() + n); LS.set("recap.day", x.toISOString().slice(0, 10)); render(); };
  const f = frag(`
    <div class="row">
      <button class="btn" id="prev">←</button><input type="date" id="day" value="${day}" style="max-width:170px"><button class="btn" id="next">→</button>
      <button class="btn ghost" id="today">Oggi</button><span class="spacer"></span>
      <button class="btn primary" id="send">📤 Invia recap su Telegram</button>
    </div>
    <div class="grid kpis section">
      ${kpi("🟢 Segnali BUY massimo", r.massimi, r.top_coins.length ? r.top_coins.map(s => s.split("/")[0]).join(", ") : "")}
      ${kpi("⏰ Preavvisi", r.preavvisi, r.precisione_preavvisi == null ? "" : `precisione ${pct(r.precisione_preavvisi, 0, false)}`)}
      ${kpi("📈 Operazioni", `${r.ops_new} / ${r.ops_closed}`, "nuove / chiuse")}
      ${kpi("💶 Risultato", eur(r.net_eur), r.ops_closed ? `✅ ${r.wins} · 🛑 ${r.losses} · ⏱️ ${r.timeouts}` : "nessuna chiusa", cls(r.net_eur))}
      ${kpi("🏆 Migliore", r.best ? esc(r.best.symbol.split("/")[0]) : "–", r.best ? pct(r.best.net_pct, 2) : "", "pos")}
      ${kpi("⚠️ Errori", r.errors_pred + r.errors_tech, `${r.errors_pred} previsione · ${r.errors_tech} tecnici`, r.errors_pred + r.errors_tech ? "neg" : "")}
    </div>
    <div class="grid cols-2 section">
      <div class="card"><h2>📋 Riepilogo</h2><div class="recap-text">${esc(r.text)}</div></div>
      <div class="card"><h2>🔄 Ancora aperte (${r.open_ops.length})</h2><div id="open"></div></div>
    </div>
    <div class="card section"><h2>🟢 Segnali della giornata</h2><div id="sigs"></div></div>
    <div class="card section"><h2>✅ Operazioni chiuse</h2><div id="closed"></div></div>
    <div class="card section"><h2>⚠️ Errori della giornata</h2><div id="errs"></div></div>`);
  $("#prev", f).onclick = () => shift(-1); $("#next", f).onclick = () => shift(1);
  $("#today", f).onclick = () => { LS.set("recap.day", todayStr()); render(); };
  $("#day", f).onchange = e => { LS.set("recap.day", e.target.value); render(); };
  $("#send", f).onclick = async e => { e.target.disabled = true; try { const x = await api("/api/recap/send", {}); toast("Recap inviato", (x.result || []).join("\n") || "Nessun canale configurato"); } catch (er) { toast("Errore", er.message, "errore"); } e.target.disabled = false; };
  $("#open", f).appendChild(table(r.open_ops, opCols(true), o => openCoin(o.symbol)));
  $("#sigs", f).innerHTML = r.signals.length ? `<div class="sig-cards">${r.signals.map(sigCard).join("")}</div>` : empty("🕐", "Nessun segnale in questa giornata");
  $$(".sig", $("#sigs", f)).forEach(el => el.onclick = () => openCoin(el.dataset.sym));
  $("#closed", f).appendChild(table(r.closed_list, opCols(false), o => openCoin(o.symbol)));
  $("#errs", f).appendChild(errTable(r.errors));
  return f;
}
function errTable(list) {
  return table(list, [
    { key: "ts", label: "Ora", fmt: v => dt(v, false) },
    { key: "category", label: "Tipo", fmt: v => v === "previsione" ? '<span class="chip red">🎯 Previsione</span>' : '<span class="chip orange">🔧 Tecnico</span>' },
    { key: "source", label: "Origine" },
    { key: "symbol", label: "Coin", fmt: v => v ? `<b>${esc(v)}</b>` : "" },
    { key: "message", label: "Dettaglio", sort: false, fmt: v => `<span style="white-space:normal">${esc(v)}</span>` }]);
}

/* ---- calendario ---- */
async function pageCalendar() {
  const now = new Date();
  const month = LS.get("cal.month", null) || `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const show = LS.get("cal.show", { pred: true, tech: true });
  const c = await api("/api/calendar?month=" + month);
  const [y, m] = month.split("-").map(Number);
  const mName = new Date(y, m - 1, 1).toLocaleDateString("it-IT", { month: "long", year: "numeric" });
  const shift = n => { const d = new Date(y, m - 1 + n, 1); LS.set("cal.month", `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`); render(); };
  let cells = "";
  for (let i = 0; i < c.first_weekday; i++) cells += '<div class="cal-day blank"></div>';
  const today = todayStr();
  for (let dd = 1; dd <= c.n_days; dd++) {
    const key = `${month}-${String(dd).padStart(2, "0")}`, x = c.days[key] || {};
    const errs = (show.pred ? x.pred || 0 : 0) + (show.tech ? x.tech || 0 : 0);
    const lvl = errs === 0 ? "" : errs <= 2 ? "lvl1" : errs <= 5 ? "lvl2" : "lvl3";
    const tags = [
      show.pred && x.pred ? `<span class="chip red">🎯 ${x.pred}</span>` : "",
      show.tech && x.tech ? `<span class="chip orange">🔧 ${x.tech}</span>` : "",
      x.wins ? `<span class="chip green">✅ ${x.wins}</span>` : "",
      x.massimi ? `<span class="chip blue">🟢 ${x.massimi}</span>` : ""].join("");
    cells += `<div class="cal-day ${lvl} ${key === today ? "today" : ""}" data-day="${key}"><span class="n">${dd}</span><div class="tags">${tags}</div>${x.net_eur ? `<span class="eur ${cls(x.net_eur)}">${eur(x.net_eur)}</span>` : ""}</div>`;
  }
  const t = c.totals;
  const f = frag(`
    <div class="row">
      <button class="btn" id="prev">←</button><b style="font-size:17px;text-transform:capitalize;min-width:170px;text-align:center">${mName}</b><button class="btn" id="next">→</button>
      <button class="btn ghost" id="cur">Mese corrente</button><span class="spacer"></span>
      <label class="switch"><input type="checkbox" id="sp" ${show.pred ? "checked" : ""}>🎯 Errori di previsione</label>
      <label class="switch"><input type="checkbox" id="st" ${show.tech ? "checked" : ""}>🔧 Errori tecnici</label>
      <a class="btn" href="/api/export/errori.csv">⬇ CSV</a>
    </div>
    <div class="grid kpis section">
      ${kpi("🎯 Errori di previsione", t.pred, "preavvisi annullati + segnali finiti allo STOP", t.pred ? "neg" : "")}
      ${kpi("🔧 Errori tecnici", t.tech, "exchange, rete, Telegram, bot")}
      ${kpi("🟢 Segnali BUY massimo", t.massimi, `${t.annullati} preavvisi annullati`)}
      ${kpi("💶 Risultato del mese", eur(t.net_eur), `✅ ${t.wins} · 🛑 ${t.losses}`, cls(t.net_eur))}
    </div>
    <div class="grid cols-21 section">
      <div class="card">
        <div class="cal-head">${["Lun", "Mar", "Mer", "Gio", "Ven", "Sab", "Dom"].map(x => `<div>${x}</div>`).join("")}</div>
        <div class="cal-grid">${cells}</div>
        <div class="legend section"><span>Colore = numero di errori del giorno</span><span class="chip red">🎯 previsione</span><span class="chip orange">🔧 tecnici</span><span class="chip green">✅ operazioni al SELL</span><span class="chip blue">🟢 segnali massimo</span></div>
      </div>
      <div class="card"><h2 id="dTitle">Dettaglio del giorno</h2><div id="detail">${empty("👈", "Clicca un giorno per vedere gli errori")}</div></div>
    </div>`);
  $("#prev", f).onclick = () => shift(-1); $("#next", f).onclick = () => shift(1);
  $("#cur", f).onclick = () => { LS.set("cal.month", null); render(); };
  $("#sp", f).onchange = e => { LS.set("cal.show", { ...show, pred: e.target.checked }); render(); };
  $("#st", f).onchange = e => { LS.set("cal.show", { ...show, tech: e.target.checked }); render(); };
  $$(".cal-day[data-day]", f).forEach(el => el.onclick = async () => {
    $$(".cal-day.sel").forEach(x => x.classList.remove("sel")); el.classList.add("sel");
    const det = $("#detail"); det.innerHTML = empty("⏳", "Caricamento…");
    const x = await api("/api/day?day=" + el.dataset.day);
    $("#dTitle").textContent = "📅 " + new Date(el.dataset.day + "T12:00:00").toLocaleDateString("it-IT", { weekday: "long", day: "numeric", month: "long" });
    const errs = x.errors.filter(e => (show.pred || e.category !== "previsione") && (show.tech || e.category !== "tecnico"));
    det.innerHTML = `<div class="row"><span class="chip red">🎯 ${x.recap.errors_pred}</span><span class="chip orange">🔧 ${x.recap.errors_tech}</span><span class="chip green">✅ ${x.recap.wins}</span><span class="chip red">🛑 ${x.recap.losses}</span><span class="${cls(x.recap.net_eur)}"><b>${eur(x.recap.net_eur)}</b></span></div>
      <div class="feed section">${errs.length ? errs.map(e => `<div class="ev"><div class="ico">${e.category === "previsione" ? "🎯" : "🔧"}</div><div><b>${esc(e.symbol || e.source)}</b> <time>${dt(e.ts, false)} · ${esc(e.source)}</time><p>${esc(e.message)}</p></div></div>`).join("") : empty("🎉", "Nessun errore in questo giorno")}</div>
      ${x.closed.length ? `<h2 class="section">Operazioni chiuse</h2>` : ""}<div id="dc"></div>
      <div class="row section"><button class="btn small" id="toRecap">📋 Apri il recap di questo giorno</button></div>`;
    if (x.closed.length) $("#dc").appendChild(table(x.closed, opCols(true), o => openCoin(o.symbol)));
    $("#toRecap").onclick = () => { LS.set("recap.day", el.dataset.day); location.hash = "#/recap"; };
  });
  return f;
}

/* ---- radar ---- */
async function pageRadar() {
  const r = await api("/api/radar");
  const f = frag(`<div class="row"><span class="muted">${r.at ? `Scansione del ${dt(r.at)} · ${esc(r.exchange)} · candele ${esc(r.timeframe)} chiuse` : "Nessuna scansione ancora: premi ⟳ in alto."}</span><span class="spacer"></span><input type="text" id="q" placeholder="Cerca coin…" style="max-width:200px"></div><div class="card section"><div id="t"></div></div>`);
  const rows = r.rows || [], holder = $("#t", f);
  const draw = q => { holder.innerHTML = ""; holder.appendChild(table(rows.filter(x => !q || x.symbol.toLowerCase().includes(q.toLowerCase())), radarCols(false), x => openCoin(x.symbol))); };
  draw(""); $("#q", f).oninput = e => draw(e.target.value);
  return f;
}

/* ---- bot ---- */
async function pageBot() {
  const b = await api("/api/bot");
  const st = b.status || {};
  const f = frag(`
    <div class="grid cols-21">
      <div class="card"><h2>🤖 Collega il bot Telegram</h2>
        <div class="steps">
          <div class="step ${b.has_token ? "done" : ""}"><div><b>Crea il bot</b><p class="muted" style="margin:4px 0 8px">Su Telegram apri <b>@BotFather</b>, scrivi <code>/newbot</code> e incolla qui il token.</p>
            <div class="row"><input type="password" id="token" placeholder="${b.has_token ? "token salvato (cifrato) – incollane uno nuovo per cambiarlo" : "123456:ABC-…"}" style="max-width:380px"><button class="btn primary" id="saveTok">Salva</button></div>
            ${b.has_token ? (b.username ? `<p class="pos">✓ Bot <b>@${esc(b.username)}</b></p>` : '<p class="neg">Token non valido o Telegram non raggiungibile</p>') : ""}</div></div>
          <div class="step ${b.running ? "done" : ""}"><div><b>Avvia il bot</b><p class="muted" style="margin:4px 0 8px">Resta acceso in background anche chiudendo l'app: risponde ai comandi, fa il controllo orario e (se l'app è chiusa) esegue anche il motore dei segnali.</p>
            <div class="row"><button class="btn primary" id="start" ${b.running ? "disabled" : ""}>▶ Avvia</button><button class="btn danger" id="stop" ${b.running ? "" : "disabled"}>■ Ferma</button>
            <label class="switch"><input type="checkbox" id="auto" ${b.autostart ? "checked" : ""}>Avvia all'accesso a Windows</label></div></div></div>
          <div class="step ${b.linked ? "done" : ""}"><div><b>Collega il tuo account</b><p class="muted" style="margin:4px 0 8px">Apri il link e premi <b>Avvia</b>: da quel momento il bot risponde solo a te.</p>
            <div class="row">${b.link ? `<a class="btn primary" href="${esc(b.link)}" target="_blank" rel="noopener">Apri @${esc(b.username)} su Telegram</a>` : '<span class="muted">Serve prima un token valido</span>'}${b.linked ? '<span class="chip green">✓ collegato</span><button class="btn small ghost" id="unlink">Scollega</button>' : ""}</div></div></div>
        </div>
      </div>
      <div class="card"><h2>📡 Stato</h2>
        <div class="feed">
          <div class="ev"><div class="ico">${b.running ? "🟢" : b.starting ? "🟡" : "🔴"}</div><div><b>Bot ${b.running ? "in esecuzione" : b.starting ? "in avvio…" : "fermo"}</b><p>${st.pid ? "PID " + st.pid + " · " : ""}${st.avviato ? "avviato " + dt(st.avviato) : ""}</p></div></div>
          <div class="ev"><div class="ico">${st.telegram === "ok" ? "✅" : st.telegram ? "⚠️" : "•"}</div><div><b>Telegram: ${esc(st.telegram || "–")}</b><p>${st.ultimo_contatto ? "ultimo contatto " + dt(st.ultimo_contatto) : ""}${st.ultimo_errore ? "\n" + esc(st.ultimo_errore) : ""}</p></div></div>
          <div class="ev"><div class="ico">${b.engine.alive ? "⚙️" : "⛔"}</div><div><b>Motore segnali ${b.engine.alive ? "attivo" : "fermo"}</b><p>${b.engine.alive ? (b.engine.process === "bot" ? "gira nel bot" : "gira nell'app web") : ""}${b.engine.last_confirm ? "\nultima scansione " + dt(b.engine.last_confirm) : ""}</p></div></div>
          <div class="ev"><div class="ico">🕐</div><div><b>Ultimo controllo orario</b><p>${dt(st.ultimo_controllo)}</p></div></div>
        </div>
      </div>
    </div>
    <div class="card section"><h2>💬 Comandi</h2><div class="table-wrap"><table><tbody>
      ${[["/segnali", "segnali BUY massimo e preavvisi di oggi"], ["/recap", "recap della giornata"], ["/crypto", "migliori criptovalute ora"], ["/test", "test delle previsioni + Excel"], ["/compra", "migliori azioni da comprare"], ["/titolo NVDA", "analisi di un titolo"], ["/fed", "tono della Fed"], ["/avvisi", "ultimi avvisi"], ["/stato", "stato del bot"]].map(([c, d]) => `<tr><td><code>${c}</code></td><td>${d}</td></tr>`).join("")}
    </tbody></table></div></div>
    <div class="card section"><h2>📜 Registro del bot <span class="right"><button class="btn small" id="reload">Aggiorna</button></span></h2><pre class="log">${esc(b.log || "(vuoto)")}</pre><small>Avvio manuale: <code>${esc(b.command)}</code></small></div>`);
  $("#saveTok", f).onclick = async () => { const v = $("#token").value.trim(); if (!v) return; try { const r = await api("/api/bot/token", { token: v }); toast(r.username ? "Token salvato: @" + r.username : "Token salvato ma non valido", "", r.username ? "info" : "errore"); render(); } catch (e) { toast("Errore", e.message, "errore"); } };
  $("#start", f).onclick = async () => { await api("/api/bot/start", {}); toast("Avvio del bot…", "La prima volta può richiedere fino a un minuto."); setTimeout(render, 4000); };
  $("#stop", f).onclick = async () => { await api("/api/bot/stop", {}); toast("Arresto del bot richiesto"); setTimeout(render, 3000); };
  $("#auto", f).onchange = async e => { try { await api("/api/bot/autostart", { enabled: e.target.checked }); } catch (er) { toast("Errore", er.message, "errore"); } };
  if ($("#unlink", f)) $("#unlink", f).onclick = async () => { await api("/api/bot/unlink", {}); render(); };
  $("#reload", f).onclick = () => render();
  return f;
}

/* ---- investimento automatico ---- */
const AUTO_FIELDS = [
  ["auto_percentuale", "Quanto investire per operazione (%)", "Percentuale della liquidità disponibile"],
  ["auto_importo_max", "Massimo per operazione (USDC)", "0 = nessun limite"],
  ["auto_importo_min", "Minimo per operazione (USDC)", "Sotto questo importo il segnale si salta (minimo exchange 5)"],
  ["auto_max_trade_giorno", "Acquisti al giorno (massimo)", "Raggiunto il limite, i segnali di oggi non si comprano"],
  ["auto_max_posizioni", "Operazioni aperte insieme (massimo)", ""],
  ["auto_stop_perdita_giorno", "Stop perdita giornaliera (USDC)", "Se oggi si perde più di così, niente acquisti fino a domani (0 = mai)"],
  ["auto_riserva", "Riserva mai investita (USDC)", "Soldi che restano sempre liquidi"],
  ["auto_spread_max", "Liquidità: differenza massima acquisto/vendita (%)", "Se il libro ordini è più largo di così la coin non si compra (prezzi che saltano)"],
];
const POS_STATUS = { ordine: ["⏳ Ordine BUY", "gray"], aperta: ["🔄 Aperta", "blue"], chiusa: ["✔ Chiusa", "green"], annullata: ["✖ Annullata", "gray"], errore: ["⚠️ Errore", "red"] };
function posChip(p) {
  if (p.status === "chiusa") return `<span class="chip ${p.pnl >= 0 ? "green" : "red"}">${p.reason && p.reason.startsWith("SELL") ? "🎯 SELL" : p.reason === "STOP" ? "🛑 STOP" : "✔ " + esc(p.reason || "chiusa")}</span>`;
  const s = POS_STATUS[p.status] || [p.status, "gray"]; return `<span class="chip ${s[1]}">${s[0]}</span>`;
}
function usdt(v, sign = true) { v = num(v); return v === null ? "–" : (sign && v > 0 ? "+" : "") + v.toFixed(2).replace(".", ",") + " $"; }
function usd(v) { v = num(v); return v === null ? "–" : v.toLocaleString("it-IT", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " $"; }

async function pageAuto() {
  const d = await api("/api/auto");
  const st = d.status, w = st.wallet, t = st.today, s = d.settings, q = d.quote || "USDT";
  const real = st.mode === "reale", k = st.keys;
  const gain = w.realized + w.unrealized, base = w.deposited || 0;
  const free = real ? st.exchange_free : w.cash;
  const pos = d.history.positions, open = pos.filter(p => ["ordine", "aperta"].includes(p.status)), done = pos.filter(p => !["ordine", "aperta"].includes(p.status));
  const sum = d.history.summary;
  const f = frag(`
    <div class="card auto-hero ${st.active ? "on" : ""} ${real ? "real" : ""}">
      <div class="auto-mode">${real ? '<span class="chip red">💶 SOLDI VERI</span>' : '<span class="chip blue">🧪 SIMULAZIONE</span>'}
        <span class="muted">${real ? `ordini reali su ${esc(d.exchange)}` : "soldi virtuali, prezzi reali"}</span></div>
      <div class="auto-main">
        <label class="switch big"><input type="checkbox" id="autoOn" ${st.active ? "checked" : ""}><span>Investimento automatico <b>${st.active ? "ATTIVO" : "SPENTO"}</b></span></label>
        <p class="muted">${st.active ? `Ogni segnale BUY massimo viene comprato da solo. Prossima operazione: <b>${st.next_size ? usd(st.next_size) : "–"}</b>${st.next_size_reason ? " · " + esc(st.next_size_reason) : ""}` : "Accendilo per comprare automaticamente i prossimi segnali BUY massimo, entro le regole qui sotto."}</p>
      </div>
      <div class="row"><button class="btn danger" id="stopAll">⛔ Ferma tutto</button></div>
    </div>
    ${!d.engine_alive ? '<div class="banner warn section">⚠️ Il motore dei segnali non è attivo: tieni aperta l\'app o avvia il bot Telegram, altrimenti non si compra e lo STOP non viene controllato.</div>' : ""}
    ${st.blocked && st.active ? `<div class="banner warn section">⏸️ Acquisti in pausa: ${esc(st.blocked)}</div>` : ""}
    ${real && st.exchange_error ? `<div class="banner warn section">⚠️ Saldo dell'exchange non letto: ${esc(st.exchange_error)}</div>` : ""}
    <div class="grid kpis section">
      ${kpi("💼 Valore totale", usd(real && st.exchange_value != null ? st.exchange_value : w.value), real ? "liquidità + operazioni aperte" : "versato + risultati")}
      ${kpi("💶 Versato", usd(w.deposited), `${d.history.deposits.length} versamenti registrati`)}
      ${kpi("📈 Guadagno", usdt(gain), base ? pct(gain / base * 100) + " sul versato" : "chiuse + aperte", cls(gain))}
      ${kpi("💧 Liquidità disponibile", free == null ? "–" : usd(free), real ? "USDT liberi sull'exchange" : "pronta per i prossimi segnali")}
      ${kpi("🛒 Acquisti oggi", `${t.trades} / ${s.auto_max_trade_giorno}`, `${t.open} aperte (max ${s.auto_max_posizioni})`)}
      ${kpi("📉 Risultato oggi", usdt(t.pnl), s.auto_stop_perdita_giorno > 0 ? `stop a -${s.auto_stop_perdita_giorno} USDT` : "nessuno stop giornaliero", cls(t.pnl))}
    </div>
    <div class="grid cols-21 section">
      ${chartCard("📊 Versato e valore nel tempo", "cWallet")}
      <div class="card"><h2>🎯 Risultati</h2><div class="feed">
        <div class="ev"><div class="ico">✔</div><div><b>${sum.closed} operazioni chiuse</b><p>${sum.win_rate == null ? "ancora nessuna" : `${pct(sum.win_rate, 0, false)} in guadagno (${sum.wins} su ${sum.closed})`}</p></div></div>
        <div class="ev"><div class="ico">💰</div><div><b class="${cls(sum.pnl)}">${usdt(sum.pnl)}</b><p>risultato delle operazioni chiuse</p></div></div>
        <div class="ev"><div class="ico">🏆</div><div><b>Migliore ${usdt(sum.best)} · peggiore ${usdt(sum.worst)}</b></div></div>
      </div></div>
    </div>
    <div class="card section"><h2>🔄 Operazioni in corso</h2><div id="openPos"></div></div>
    <div class="card section"><h2>📜 Storico operazioni</h2><div id="donePos"></div></div>
    <div class="grid cols-2 section">
      <div class="card"><h2>💶 Versamenti</h2>
        <p class="muted">${real ? `Versa sull'exchange e converti in ${esc(q)}: il radar investe da solo la liquidità libera. Registra qui i versamenti per calcolare il guadagno.` : "Capitale virtuale: aggiungi i soldi che verseresti davvero."}</p>
        <div class="row"><input type="number" id="depAmt" step="any" placeholder="Importo (${esc(q)})" style="max-width:160px"><input type="text" id="depNote" placeholder="Nota (facoltativa)" style="max-width:200px"><button class="btn primary" id="depAdd">+ Versa</button><button class="btn ghost" id="depOut">− Preleva</button></div>
        <div id="deps" class="section"></div>
      </div>
      <div class="card"><h2>🗓️ Piano di versamento periodico</h2>
        <form id="planForm" class="form">
          <div class="field"><label>Importo per versamento</label><input type="number" step="any" name="auto_piano_importo" value="${esc(s.auto_piano_importo)}"><small>0 = nessun piano</small></div>
          <div class="field"><label>Frequenza</label><select name="auto_piano_frequenza">${["mensile", "settimanale"].map(o => `<option ${o === s.auto_piano_frequenza ? "selected" : ""}>${o}</option>`).join("")}</select></div>
          <div class="field"><label>Giorno</label><input type="number" name="auto_piano_giorno" min="1" max="28" value="${esc(s.auto_piano_giorno)}"><small>Del mese (1-28) o della settimana (1 = lunedì)</small></div>
        </form>
        <p class="muted">${real ? "Il giorno del versamento ricevi un promemoria su Telegram." : "In simulazione il versamento viene aggiunto da solo il giorno stabilito."}</p>
        <button class="btn primary" id="planSave">💾 Salva piano</button>
      </div>
    </div>
    <div class="card section"><h2>🛡️ Regole e limiti</h2>
      <form id="rulesForm" class="form">
        ${AUTO_FIELDS.map(([key, label, help]) => `<div class="field"><label>${label}</label><input type="number" step="any" name="${key}" value="${esc(s[key])}">${help ? `<small>${help}</small>` : ""}</div>`).join("")}
        <div class="field"><label class="switch"><input type="checkbox" name="auto_solo_preavvisati" ${s.auto_solo_preavvisati ? "checked" : ""}>Compra solo i segnali preavvisati</label><small>Solo i segnali arrivati dopo il preavviso confermato (meno operazioni, più selettive)</small></div>
      </form>
      <div class="row section"><button class="btn primary" id="rulesSave">💾 Salva regole</button><span class="muted">Si comprano i segnali BUY massimo (soglia e probabilità nelle Impostazioni), i più forti per primi.</span></div>
    </div>
    <div class="grid cols-2 section">
      <div class="card"><h2>🔑 Collegamento all'exchange (${esc(d.exchange)})</h2>
        ${k.configured ? `<p class="${k.verified ? "pos" : "neg"}">${k.verified ? "✓ Chiave verificata" : "Chiave non verificata"} · ${esc(k.key_hint)} · ${esc(k.exchange)} ${k.withdraw === false ? "· prelievi disattivati ✓" : ""}</p><button class="btn small ghost" id="keyDel">Elimina chiavi</button>` : ""}
        <ol class="muted howto">
          <li>Su ${esc(d.exchange)} vai in <b>Gestione API</b> e crea una chiave nuova.</li>
          <li>Abilita solo <b>Trading spot</b>. <b>Lascia disattivati i prelievi</b> (le chiavi che possono prelevare vengono rifiutate).</li>
          <li>Se puoi, limita la chiave all'indirizzo IP di casa.</li>
        </ol>
        <div class="form">
          <div class="field"><label>API key</label><input type="password" id="kKey" autocomplete="off"></div>
          <div class="field"><label>Secret</label><input type="password" id="kSec" autocomplete="off"></div>
          <div class="field"><label>Passphrase</label><input type="password" id="kPwd" autocomplete="off"><small>Solo per OKX/KuCoin</small></div>
        </div>
        <div class="row section"><button class="btn" id="kSave">🔐 Verifica e salva</button><small class="muted">Salvate cifrate su questo PC (Windows DPAPI), mai mostrate.</small></div>
      </div>
      <div class="card"><h2>⚖️ Modalità</h2>
        <div class="mode-cards">
          <div class="mode-card ${!real ? "sel" : ""}"><b>🧪 Simulazione</b><p class="muted">Soldi virtuali sui prezzi veri: vedi come andrebbe senza rischiare nulla. Consigliata per almeno 2-4 settimane.</p>${real ? '<button class="btn small" id="toSim">Passa alla simulazione</button>' : '<span class="chip blue">in uso</span>'}</div>
          <div class="mode-card ${real ? "sel" : ""}"><b>💶 Soldi veri</b><p class="muted">Ordini reali sull'exchange. Puoi perdere i soldi investiti: usa solo quanto puoi permetterti di perdere.</p>
            ${real ? '<span class="chip red">in uso</span>' : `<div class="row"><input type="text" id="confirmWord" placeholder="scrivi ${esc(d.confirm_word)}" style="max-width:150px"><button class="btn danger" id="toReal" ${k.verified ? "" : "disabled title='collega prima le chiavi'"}>Usa soldi veri</button></div>`}
          </div>
        </div>
        <p class="muted section">Lo STOP viene controllato dal radar ogni 15 secondi: il PC deve restare acceso con l'app o il bot Telegram attivi. Il SELL resta impostato sull'exchange anche a PC spento.</p>
      </div>
    </div>`);
  const posCols = [
    { key: "symbol", label: "Coin", fmt: v => `<b>${esc(v)}</b>` },
    { key: "status", label: "Stato", fmt: (v, p) => posChip(p) },
    { key: "created", label: "Segnale", fmt: v => dt(v) },
    { key: "cost", label: "Investito", num: true, fmt: v => usd(v) },
    { key: "entry", label: "Prezzo BUY", num: true, fmt: (v, p) => price(v ?? p.buy) },
    { key: "last", label: "Ora", num: true, fmt: (v, p) => price(p.exit ?? v) },
    { key: "sell", label: "SELL / STOP", fmt: (v, p) => `${price(v)} / ${price(p.stop)}` },
    { key: "pnl", label: "Risultato", num: true, sortVal: p => p.pnl ?? (p.qty && p.last ? p.qty * p.last * 0.999 - p.cost : null),
      fmt: (v, p) => { const x = v ?? (p.status === "aperta" && p.last ? p.qty * p.last * 0.999 - p.cost : null); return `<span class="${cls(x)}">${usdt(x)}${p.cost && x != null ? ` (${pct(x / p.cost * 100)})` : ""}</span>`; } },
  ];
  $("#openPos", f).appendChild(table(open, posCols, p => openCoin(p.symbol)));
  $("#donePos", f).appendChild(table(done, [...posCols.slice(0, 2), { key: "closed_at", label: "Chiusa", fmt: (v, p) => v ? dt(v) : esc(p.reason || "") }, ...posCols.slice(3)], p => openCoin(p.symbol)));
  const deps = d.history.deposits;
  $("#deps", f).innerHTML = deps.length ? `<div class="table-wrap"><table><tbody>${deps.map(x => `<tr><td>${dt(x.ts)}</td><td class="num ${cls(x.amount)}">${usdt(x.amount)}</td><td>${esc(x.note || "")}</td><td><button class="btn small ghost" data-del="${x.id}">✕</button></td></tr>`).join("")}</tbody></table></div>` : empty("💶", real ? "Nessun versamento registrato" : "Aggiungi un versamento virtuale per iniziare la simulazione");
  $$("[data-del]", f).forEach(b => b.onclick = async () => { if (!confirm("Eliminare questo versamento?")) return; await api("/api/auto/deposit", { delete: +b.dataset.del }); render(true); });

  const post = async (path, body, okMsg) => { try { const r = await api(path, body); if (okMsg) toast(okMsg); render(true); return r; } catch (e) { toast("Non eseguito", e.message, "errore"); render(true); } };
  $("#autoOn", f).onchange = e => {
    if (e.target.checked && real && !confirm("Attivare l'investimento automatico con SOLDI VERI?\nI prossimi segnali verranno comprati senza chiederti conferma.")) { e.target.checked = false; return; }
    post("/api/auto/active", { active: e.target.checked }, e.target.checked ? "Investimento automatico attivato" : "Investimento automatico spento");
  };
  $("#stopAll", f).onclick = () => {
    const sell = open.length && confirm(`Fermo i nuovi acquisti.\n\nVuoi anche VENDERE SUBITO le ${open.length} operazioni in corso al prezzo di mercato?\n\nOK = vendi tutto · Annulla = tienile (restano protette da SELL e STOP)`);
    post("/api/auto/stop", { close_all: !!sell }, sell ? "Fermato: vendita di tutte le posizioni in corso…" : "Investimento automatico fermato");
  };
  const depo = sign => { const a = +$("#depAmt").value; if (!a || a <= 0) return toast("Inserisci un importo", "", "errore"); post("/api/auto/deposit", { amount: sign * a, note: $("#depNote").value }, sign > 0 ? "Versamento registrato" : "Prelievo registrato"); };
  $("#depAdd", f).onclick = () => depo(1);
  $("#depOut", f).onclick = () => depo(-1);
  const formBody = form => { const b = {}; $$("[name]", form).forEach(i => b[i.name] = i.type === "checkbox" ? i.checked : i.type === "number" ? +i.value : i.value); return b; };
  $("#rulesSave", f).onclick = () => post("/api/auto/settings", formBody($("#rulesForm")), "Regole salvate");
  $("#planSave", f).onclick = () => post("/api/auto/settings", formBody($("#planForm")), "Piano salvato");
  $("#kSave", f).onclick = async () => {
    const b = { key: $("#kKey").value, secret: $("#kSec").value, password: $("#kPwd").value };
    if (!b.key || !b.secret) return toast("Inserisci API key e secret", "", "errore");
    toast("Verifica delle chiavi…", "", "info", 3000);
    const r = await post("/api/auto/keys", b); if (r) toast("Chiavi verificate ✅", `Saldo libero: ${usd(r.free)}`);
  };
  if ($("#keyDel", f)) $("#keyDel", f).onclick = () => { if (confirm("Eliminare le chiavi API? L'investimento torna in simulazione.")) post("/api/auto/keys", { delete: true }, "Chiavi eliminate"); };
  if ($("#toSim", f)) $("#toSim", f).onclick = () => post("/api/auto/mode", { mode: "simulazione" }, "Modalità simulazione");
  if ($("#toReal", f)) $("#toReal", f).onclick = () => {
    if (!confirm("Da ora gli acquisti useranno SOLDI VERI sul tuo conto.\nL'investimento resta spento finché non lo riaccendi. Continuare?")) return;
    post("/api/auto/mode", { mode: "reale", confirm: $("#confirmWord").value }, "Modalità SOLDI VERI: riaccendi l'investimento quando vuoi");
  };
  f.addEventListener("mounted", () => {
    const days = d.history.days, cv = $("#cWallet");
    if (!days.length) { cv.parentElement.innerHTML = empty("📊", "Il grafico si riempie giorno per giorno"); return; }
    chart(cv, { type: "line", legend: true, data: { labels: days.map(x => x.day.slice(5).split("-").reverse().join("/")), datasets: [
      { label: "Versato", data: days.map(x => x.deposits), borderColor: css("--muted"), borderDash: [5, 4], pointRadius: 0, stepped: true },
      { label: "Valore", data: days.map(x => x.value), borderColor: css("--green"), backgroundColor: css("--green-bg"), fill: true, pointRadius: 2, tension: .25 }] } });
  });
  return f;
}

/* ---- impostazioni ---- */
const FIELDS = [
  ["🟢 Segnale BUY massimo", [
    ["segnali_attivi", "bool", "Motore dei segnali attivo"],
    ["strategia", "select", "Strategia", "rimbalzo = compra le coin crollate sotto la banda di Bollinger solo con BTC in salita, vende alla media (da provare in simulazione) · dip = compra il ribasso (RSI basso in un trend rialzista) · trend = punteggio sopra la soglia"],
    ["rimb_banda", "number", "Rimbalzo: deviazioni sotto la media", "3,5 = solo crolli forti (meno operazioni, migliori nei test) · 3 = più operazioni"],
    ["rimb_stop_atr", "number", "Rimbalzo: STOP (multipli di ATR)", "Distanza dello STOP sotto il prezzo d'ingresso"],
    ["rimb_candele_max", "number", "Rimbalzo: durata massima (candele)", "Se né SELL né STOP arrivano entro queste candele, vende al prezzo di mercato"],
    ["dip_rsi", "number", "Dip: soglia RSI", "Il segnale scatta quando l'RSI scende sotto questo valore"],
    ["dip_stop_atr", "number", "Dip: STOP (multipli di ATR)", "Distanza dello STOP sotto il prezzo d'ingresso"],
    ["dip_target_atr", "number", "Dip: SELL (multipli di ATR)", "Distanza del SELL sopra il prezzo d'ingresso"],
    ["dip_candele_max", "number", "Dip: durata massima (candele)", "Se né SELL né STOP arrivano entro queste candele, vende al prezzo di mercato"],
    ["segnali_exchange", "select", "Exchange", "Dall'Italia: bybiteu, myokx (OKX EU), kraken, bitvavo, coinbase, cryptocom (licenza MiCA)"],
    ["segnali_quote", "select", "Valuta di quotazione", "In UE (MiCA) USDT non è negoziabile: usa USDC o EUR"],
    ["segnali_timeframe", "select", "Candele", "Il preavviso arriva prima della chiusura di ogni candela"],
    ["segnali_coins", "number", "Coin controllate", "Le più scambiate dell'exchange"],
    ["segnali_volume_min", "number", "Volume minimo 24 h", "Le coin con meno scambi non vengono seguite (poco liquide)"],
    ["soglia_massimo", "number", "Soglia segnale massimo (punteggio)", "50 = 'FORTE RIALZO'. Più alta = meno segnali ma più forti"],
    ["probabilita_minima", "number", "Probabilità minima SELL (%)", "Storia reale + Time Machine"],
    ["pausa_stesso_simbolo_ore", "number", "Pausa sulla stessa coin (ore)", "Evita avvisi ripetuti sulla stessa coin"]]],
  ["⏰ Preavviso", [
    ["preavviso_minuti", "number", "Minuti di anticipo", "Es. 20 = alle hh:40 per le candele da 1 ora (massimo metà candela)"],
    ["soglia_preavviso", "number", "Soglia del preavviso (punteggio provvisorio)", "Più bassa della soglia massima: avvisa quando ci si sta arrivando"],
    ["notifica_preavviso", "bool", "Manda il preavviso anche su Telegram/desktop"]]],
  ["🔔 Notifiche e recap", [
    ["notifiche_desktop", "bool", "Notifiche di Windows"],
    ["riepilogo_giornaliero", "bool", "Recap della giornata automatico"],
    ["riepilogo_ora", "time", "Ora del recap"],
    ["test_previsioni", "bool", "Test delle previsioni (Excel) ogni ora"]]],
  ["💰 Capitale e rischio", [
    ["capitale", "number", "Capitale (valuta di quotazione)", "0 = non calcolare la quantità"],
    ["rischio_max_operazione", "number", "Perdita massima per operazione"],
    ["posizione_max_pct", "number", "Massimo in una sola coin (%)"],
    ["riserva_cassa_pct", "number", "Liquidità di riserva (%)"]]],
];
async function pageSettings() {
  const { settings: s, choices } = await api("/api/settings");
  const field = ([k, type, label, help]) => {
    let input;
    if (type === "bool") return `<div class="field"><label class="switch"><input type="checkbox" name="${k}" ${s[k] ? "checked" : ""}>${label}</label>${help ? `<small>${help}</small>` : ""}</div>`;
    if (type === "select") input = `<select name="${k}">${choices[k].map(o => `<option ${o === s[k] ? "selected" : ""}>${o}</option>`).join("")}</select>`;
    else input = `<input type="${type}" name="${k}" value="${esc(s[k])}" ${type === "number" ? 'step="any"' : ""}>`;
    return `<div class="field"><label>${label}</label>${input}${help ? `<small>${help}</small>` : ""}</div>`;
  };
  const f = frag(`
    <form id="form">${FIELDS.map(([t, fs]) => `<div class="card section"><h2>${t}</h2><div class="form">${fs.map(field).join("")}</div></div>`).join("")}
      <div class="card section"><h2>🌐 Questo browser</h2><div class="form">
        <div class="field"><label class="switch"><input type="checkbox" id="bn" ${LS.get("browserNotif", true) ? "checked" : ""}>Notifiche del browser</label><small>Stato: ${"Notification" in window ? Notification.permission : "non supportate"}</small></div>
        <div class="field"><label class="switch"><input type="checkbox" id="snd" ${LS.get("sound", true) ? "checked" : ""}>Suono per preavviso e segnale</label></div>
      </div></div>
      <div class="row section"><button class="btn primary" type="submit">💾 Salva impostazioni</button><button class="btn" type="button" id="testN">🔔 Prova notifiche</button><button class="btn" type="button" id="perm">Consenti notifiche del browser</button></div>
    </form>`);
  $("#form", f).onsubmit = async e => {
    e.preventDefault();
    const body = {};
    $$("[name]", e.target).forEach(i => body[i.name] = i.type === "checkbox" ? i.checked : i.type === "number" ? +i.value : i.value);
    try { const r = await api("/api/settings", body); toast("Impostazioni salvate", r.changed.length ? r.changed.join(", ") : "nessuna modifica"); refreshOverview(); }
    catch (er) { toast("Non salvato", er.message, "errore"); }
  };
  $("#bn", f).onchange = e => LS.set("browserNotif", e.target.checked);
  $("#snd", f).onchange = e => { LS.set("sound", e.target.checked); if (e.target.checked) beep("massimo"); };
  $("#testN", f).onclick = async () => { const r = await api("/api/notify-test", {}); toast("Prova inviata", (r.result || []).join("\n") || "Telegram non configurato, notifiche desktop disattivate"); };
  $("#perm", f).onclick = askPermission;
  return f;
}

/* ---- scheda della coin (grafico) ---- */
async function openCoin(sym) {
  $("#modal").classList.remove("hidden");
  $("#modalTitle").textContent = sym;
  const body = $("#modalBody");
  body.innerHTML = empty("⏳", "Caricamento del grafico…");
  try {
    const d = await api("/api/coin?symbol=" + encodeURIComponent(sym));
    const p = d.plan || {};
    body.innerHTML = `
      <div class="grid kpis">${kpi("Prezzo", price(p.price ?? d.candles.at(-1)?.close))}${kpi("Punteggio", p.score == null ? "–" : p.score.toFixed(0), esc(p.signal || ""))}${kpi("Prob. SELL", pct(p.probability, 0, false), p.mc_verdict ? esc(p.mc_verdict) : "")}${kpi("Storico", p.bt_win_rate == null ? "–" : pct(p.bt_win_rate, 0, false), p.bt_trades ? `${p.bt_trades} segnali passati` : "")}</div>
      <div class="levels section"><div><small>🟢 BUY</small><b>${price(p.buy)}</b></div><div><small>🎯 SELL ${pct(p.upside_pct)}</small><b class="pos">${price(p.sell)}</b></div><div><small>🛑 STOP</small><b class="neg">${price(p.stop)}</b></div></div>
      <div id="chart" class="section"></div>
      ${p.alerts ? `<p class="muted">Allerte: ${esc(p.alerts)}</p>` : ""}
      <h2 class="section" style="font-size:15px">Segnali su questa coin</h2><div id="cs"></div>`;
    $("#cs").appendChild(table(d.signals, [
      { key: "created", label: "Quando", fmt: v => dt(v) }, { key: "kind", label: "Tipo", fmt: kindChip },
      { key: "status", label: "Esito", fmt: v => chip(v) }, { key: "score", label: "Punteggio", num: true, fmt: scoreChip },
      { key: "buy", label: "BUY", num: true, fmt: price }]));
    if (typeof LightweightCharts === "undefined") { $("#chart").innerHTML = empty("📉", "Grafico non disponibile (libreria non caricata)"); return; }
    if (S.lwChart) { S.lwChart.remove(); S.lwChart = null; }
    const el = $("#chart");
    const ch = LightweightCharts.createChart(el, { height: 420, width: el.clientWidth, layout: { background: { color: "transparent" }, textColor: css("--muted") }, grid: { vertLines: { color: css("--line") }, horzLines: { color: css("--line") } }, timeScale: { timeVisible: true }, localization: { locale: "it-IT" } });
    S.lwChart = ch;
    const offs = -new Date().getTimezoneOffset() * 60;
    const ser = ch.addCandlestickSeries({ upColor: css("--green"), downColor: css("--red"), wickUpColor: css("--green"), wickDownColor: css("--red"), borderVisible: false });
    ser.setData(d.candles.map(c => ({ ...c, time: c.time + offs })));
    [["buy", "BUY", css("--accent")], ["sell", "SELL", css("--green")], ["stop", "STOP", css("--red")]].forEach(([k, t, col]) => { if (p[k]) ser.createPriceLine({ price: p[k], color: col, lineWidth: 2, lineStyle: 2, axisLabelVisible: true, title: t }); });
    const tf = tfMs(d.timeframe), first = d.candles[0]?.time || 0;
    const marks = d.signals.map(s => ({ time: Math.floor((s.candle_close - tf) / 1000) + offs, position: "belowBar", color: s.kind === "massimo" ? css("--green") : css("--orange"), shape: s.kind === "massimo" ? "arrowUp" : "circle", text: s.kind === "massimo" ? "BUY" : "pre" }))
      .filter(m => m.time >= first + offs).sort((a, b) => a.time - b.time);
    ser.setMarkers(marks);
    ch.timeScale().fitContent();
  } catch (e) { body.innerHTML = empty("⚠️", esc(e.message)); }
}

/* ---------------- avvio ---------------- */
function askPermission() {
  if (!("Notification" in window)) return toast("Notifiche non supportate da questo browser");
  Notification.requestPermission().then(p => { $("#notifBanner").classList.add("hidden"); toast(p === "granted" ? "Notifiche attivate ✅" : "Notifiche non consentite", p === "granted" ? "Riceverai il preavviso e il segnale anche con la pagina in background." : "Puoi attivarle dalle impostazioni del browser."); });
}
function applyTheme(t) { document.documentElement.dataset.theme = t; LS.set("theme", t); }

document.addEventListener("DOMContentLoaded", () => {
  applyTheme(LS.get("theme", window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));
  $("#themeBtn").onclick = () => { applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark"); render(true); };
  $("#menuBtn").onclick = () => $("#sidebar").classList.toggle("open");
  $("#bellBtn").onclick = () => { $("#drawer").classList.toggle("hidden"); S.unread = 0; updateBell(); };
  $("#drawerClose").onclick = () => $("#drawer").classList.add("hidden");
  $("#modalClose").onclick = () => { $("#modal").classList.add("hidden"); if (S.lwChart) { S.lwChart.remove(); S.lwChart = null; } };
  $("#modal").onclick = e => { if (e.target.id === "modal") $("#modalClose").click(); };
  document.addEventListener("keydown", e => { if (e.key === "Escape") { $("#modalClose").click(); $("#drawer").classList.add("hidden"); } });
  $("#scanBtn").onclick = async () => { try { await api("/api/scan", {}); toast("Scansione avviata", "I risultati compaiono tra qualche secondo."); setTimeout(() => render(true), 9000); } catch (e) { toast("Errore", e.message, "errore"); } };
  if ("Notification" in window && Notification.permission === "default" && !LS.get("notifDismissed", false)) $("#notifBanner").classList.remove("hidden");
  $("#enableNotif").onclick = askPermission;
  $("#dismissNotif").onclick = () => { LS.set("notifDismissed", true); $("#notifBanner").classList.add("hidden"); };
  window.addEventListener("hashchange", () => render());
  refreshOverview().then(() => render());
  pollEvents();
  setInterval(pollEvents, 5000);
  setInterval(tickCountdown, 1000);
  setInterval(() => { refreshOverview(); if (S.page === "dashboard" && $("#modal").classList.contains("hidden")) render(true); }, 60000);
});
