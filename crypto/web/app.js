'use strict';
/* Crypto tracker UI. No framework: one state object, render on change,
   live prices pushed over SSE. */

const COLORS = {
  BTC:'#f7931a', ETH:'#627eea', ETC:'#3ab83a', RVN:'#6b73b8',
  TON:'#0098ea', TRX:'#eb0029', SOL:'#14f195', XMR:'#ff6600',
};
const colorFor = t => COLORS[t] || '#7928ca';

let state = { coins: [], targets: [], settings: {}, events: [], feed: {}, icons: [] };
let iconSet = new Set();
let selected = null, rangeMin = 1440, chart = null, series = null, lastPx = {};

/* ---------- formatting (mirrors alerts.fmt_price on the server) ---------- */
function fmtPrice(p) {
  if (p === null || p === undefined || !isFinite(p)) return '—';
  const a = Math.abs(p);
  let s;
  if (a >= 1000) s = p.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
  else if (a >= 1) s = p.toFixed(4).replace(/0+$/,'').replace(/\.$/,'');
  else if (a >= 0.01) s = p.toFixed(5).replace(/0+$/,'').replace(/\.$/,'');
  else if (a >= 0.0001) s = p.toFixed(7).replace(/0+$/,'').replace(/\.$/,'');
  else s = p.toFixed(9).replace(/0+$/,'').replace(/\.$/,'');
  return s;
}
const fmtPct = x => (x === null || x === undefined || !isFinite(x)) ? '—'
  : (x >= 0 ? '+' : '') + x.toFixed(2) + '%';
const cls = x => (x === null || x === undefined || !isFinite(x)) ? 'flat' : (x > 0 ? 'up' : x < 0 ? 'dn' : 'flat');
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const stripTags = s => String(s ?? '').replace(/<[^>]*>/g, '');

function humanDur(s) {
  s = Number(s) || 0;
  if (!s) return 'none';
  if (s < 60) return s + 's';
  if (s < 3600) return Math.round(s/60) + ' min';
  return Math.round(s/3600) + 'h';
}
function ago(ts) {
  const d = Math.max(0, Math.floor(Date.now()/1000 - ts));
  if (d < 60) return d + 's ago';
  if (d < 3600) return Math.floor(d/60) + 'm ago';
  if (d < 86400) return Math.floor(d/3600) + 'h ago';
  return Math.floor(d/86400) + 'd ago';
}

async function api(path, opts) {
  const r = await fetch(path, Object.assign({headers:{'Content-Type':'application/json'}}, opts));
  const t = await r.text();
  let j = {};
  try { j = t ? JSON.parse(t) : {}; } catch { throw new Error('bad response from server'); }
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
function flash(el, txt, ok) {
  el.textContent = txt;
  el.className = 'msg ' + (ok ? 'ok' : 'bad');
  if (ok) setTimeout(() => { if (el.textContent === txt) el.textContent=''; }, 5000);
}

/* ---------- coin mark ---------- */
// A vendored logo when we have one, otherwise a coloured ticker badge. The
// fallback matters: a newly listed or renamed coin (GRAM) predates the icon
// pack, and a broken image would be worse than no logo at all.
function coinMark(ticker, size) {
  size = size || 28;
  const t = String(ticker || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const label = esc(String(ticker || '?').slice(0, 4));
  const has = iconSet.has(String(ticker || '').toUpperCase());
  return `<span class="mark" style="width:${size}px;height:${size}px;background:${colorFor(ticker)}">`
       + `<span class="markTxt" style="font-size:${Math.max(8, Math.round(size * 0.34))}px">${label}</span>`
       + (has ? `<img src="icons/${t}.svg" alt="" loading="lazy" onerror="this.remove()">` : '')
       + `</span>`;
}

/* ---------- coin cards ---------- */
function renderCoins() {
  const g = document.getElementById('coinGrid');
  g.innerHTML = state.coins.map(c => `
    <div class="coin ${selected===c.symbol?'sel':''} ${c.health&&c.health!=='ok'?'bad':''}" data-sym="${c.symbol}">
      <span class="bar" style="background:${colorFor(c.ticker)}"></span>
      <div class="head">
        ${coinMark(c.ticker, 30)}
        <span class="nm"><b>${esc(c.ticker)}</b><br>${esc(c.name)}</span>
      </div>
      <div class="px" id="px-${c.symbol}">${c.price!=null?'$'+fmtPrice(c.price):'—'}</div>
      ${c.health && c.health !== 'ok'
        ? `<span class="chg dn" title="${esc(c.health_note)}">⚠ not live</span>`
        : `<span class="chg ${cls(c.change24h)}" id="ch-${c.symbol}">${fmtPct(c.change24h)}</span>`}
    </div>`).join('');
  g.querySelectorAll('.coin').forEach(el =>
    el.onclick = () => selectCoin(el.dataset.sym));
  if (!selected && state.coins.length) selectCoin(state.coins[0].symbol);
}

function updatePrice(sym, price, ch) {
  const pe = document.getElementById('px-' + sym);
  if (pe) {
    const prev = lastPx[sym];
    pe.textContent = '$' + fmtPrice(price);
    if (prev != null && price !== prev) {
      pe.classList.remove('flash-up','flash-dn');
      void pe.offsetWidth;                       // restart the transition
      pe.classList.add(price > prev ? 'flash-up' : 'flash-dn');
      setTimeout(() => pe.classList.remove('flash-up','flash-dn'), 700);
    }
  }
  lastPx[sym] = price;
  const ce = document.getElementById('ch-' + sym);
  if (ce && ch != null) { ce.textContent = fmtPct(ch); ce.className = 'chg ' + cls(ch); }
  const c = state.coins.find(x => x.symbol === sym);
  if (c) { c.price = price; if (ch != null) c.change24h = ch; }
  if (sym === selected) updateChartHead();
  renderTargetDistances();
  const row = document.querySelector(`#coinTable tr[data-sym="${sym}"]`);
  if (row) {
    const tds = row.querySelectorAll('td');
    tds[2].textContent = '$' + fmtPrice(price);
    if (ch != null) {
      tds[3].textContent = fmtPct(ch);
      tds[3].style.color = ch > 0 ? 'var(--up)' : ch < 0 ? 'var(--down)' : 'var(--muted)';
    }
  }
}

/* ---------- chart ---------- */
function ensureChart() {
  if (chart) return;
  chart = LightweightCharts.createChart(document.getElementById('chart'), {
    layout:{ background:{color:'transparent'}, textColor:'#8b93a8', fontFamily:'JetBrains Mono, monospace' },
    grid:{ vertLines:{color:'rgba(255,255,255,.04)'}, horzLines:{color:'rgba(255,255,255,.04)'} },
    rightPriceScale:{ borderColor:'rgba(255,255,255,.08)' },
    timeScale:{ borderColor:'rgba(255,255,255,.08)', timeVisible:true, secondsVisible:false },
    crosshair:{ mode:1 },
    localization:{ priceFormatter:p => '$' + fmtPrice(p) },
    autoSize:true,
  });
  series = chart.addCandlestickSeries({
    upColor:'#34d399', downColor:'#f87171', borderVisible:false,
    wickUpColor:'#34d399', wickDownColor:'#f87171',
  });
}
async function loadChart() {
  if (!selected) return;
  ensureChart();
  try {
    const j = await api(`api/candles?symbol=${encodeURIComponent(selected)}&minutes=${rangeMin}`);
    const rows = (j.candles||[]).map(c => ({time:c.ts, open:c.o, high:c.h, low:c.l, close:c.c}));
    series.setData(rows);
    // Price needs enough decimals for RVN without wrecking BTC.
    const last = rows.length ? rows[rows.length-1].close : 1;
    const dec = last >= 1000 ? 2 : last >= 1 ? 4 : last >= 0.01 ? 5 : 7;
    series.applyOptions({priceFormat:{type:'price', precision:dec, minMove:Math.pow(10,-dec)}});
    chart.timeScale().fitContent();
    document.getElementById('chartHint').textContent =
      rows.length ? `${rows.length} one-minute candles` : 'No data yet — backfilling from Binance.';
  } catch (e) {
    document.getElementById('chartHint').textContent = 'Chart failed: ' + e.message;
  }
}
function updateChartHead() {
  const c = state.coins.find(x => x.symbol === selected);
  if (!c) return;
  document.getElementById('chartTitle').innerHTML =
    `${coinMark(c.ticker, 32)}<span>${esc(c.name)}</span>`;
  const bits = [`$${fmtPrice(c.price)}`, `24h ${fmtPct(c.change24h)}`];
  if (c.high) bits.push(`H $${fmtPrice(c.high)}`);
  if (c.low)  bits.push(`L $${fmtPrice(c.low)}`);
  document.getElementById('chartSub').textContent = bits.join('  ·  ');
}
function selectCoin(sym) {
  selected = sym;
  document.querySelectorAll('.coin').forEach(e =>
    e.classList.toggle('sel', e.dataset.sym === sym));
  updateChartHead();
  loadChart();
}

/* ---------- targets ---------- */
function renderTargets() {
  const tb = document.querySelector('#targetTable tbody');
  const rows = state.targets;
  document.getElementById('noTargets').style.display = rows.length ? 'none' : 'block';
  tb.innerHTML = rows.map(t => {
    const c = state.coins.find(x => x.symbol === t.symbol) || {ticker:t.symbol};
    const up = t.direction === 'above';
    const stateLbl = !t.enabled ? '<span class="pill off">done</span>'
      : t.armed ? '<span class="pill armed">armed</span>'
                : '<span class="pill waiting">waiting</span>';
    return `<tr data-id="${t.id}">
      <td><span class="cellCoin">${coinMark(c.ticker, 24)}<b>${esc(c.ticker)}</b></span></td>
      <td class="cond"><span class="${up?'up':'dn'}">${up?'▲':'▼'}</span> $${fmtPrice(t.price)}
          ${t.repeat?'<span class="pill">repeat</span>':''} ${stateLbl}</td>
      <td class="num dist" id="dist-${t.id}">—</td>
      <td>${esc(t.note)||'<span style="color:var(--muted)">—</span>'}</td>
      <td class="num">${t.fire_count||0}</td>
      <td style="text-align:right"><button class="iconbtn" data-del="${t.id}" title="Delete">✕</button></td>
    </tr>`;
  }).join('');
  tb.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
    if (!confirm('Delete this target?')) return;
    await api('api/targets/' + b.dataset.del, {method:'DELETE'});
    refresh();
  });
  renderTargetDistances();
}
function renderTargetDistances() {
  state.targets.forEach(t => {
    const el = document.getElementById('dist-' + t.id);
    if (!el) return;
    const c = state.coins.find(x => x.symbol === t.symbol);
    if (!c || c.price == null) { el.textContent = '—'; return; }
    const d = (t.price - c.price) / c.price * 100;
    el.textContent = fmtPct(d);
    el.style.color = Math.abs(d) < 1 ? 'var(--fg)' : 'var(--muted)';
  });
}

/* ---------- coin management ---------- */
function renderCoinManager() {
  const tb = document.querySelector('#coinTable tbody');
  if (!tb) return;
  tb.innerHTML = state.coins.map(c => {
    const n = state.targets.filter(t => t.symbol === c.symbol).length;
    return `<tr data-sym="${c.symbol}">
      <td><span class="cellCoin">${coinMark(c.ticker, 26)}
          <span><b>${esc(c.ticker)}</b> <span class="nm">${esc(c.name)}</span></span></span></td>
      <td class="num" style="color:var(--muted)">${esc(c.symbol)}
          <span class="pill ${c.source === 'kraken' ? 'kraken' : ''}">${esc(c.source || 'binance')}</span></td>
      <td class="num">${c.price != null ? '$' + fmtPrice(c.price) : '—'}</td>
      <td class="num" style="color:${c.change24h > 0 ? 'var(--up)' : c.change24h < 0 ? 'var(--down)' : 'var(--muted)'}">${fmtPct(c.change24h)}</td>
      <td>${c.health && c.health !== 'ok'
            ? `<span class="pill halted" title="${esc(c.health_note)}">${esc(c.health)}</span>`
            : '<span class="pill ok">live</span>'}</td>
      <td class="num">${n || '<span style="color:var(--muted)">0</span>'}</td>
      <td style="text-align:right">
        ${c.health && c.health !== 'ok'
          ? `<button class="iconbtn swap" data-swap="${c.symbol}" title="Replace with its successor, keeping targets">⇄</button>`
          : ''}
        <button class="iconbtn" data-rm="${c.symbol}" title="Stop tracking">✕</button></td>
    </tr>`;
  }).join('');
  tb.querySelectorAll('[data-swap]').forEach(b => b.onclick = async () => {
    const sym = b.dataset.swap;
    const c = state.coins.find(x => x.symbol === sym) || {};
    const next = prompt(
      `${sym} is no longer trading (${c.health_note || 'halted'}).\n\n` +
      'Enter the ticker or symbol that replaced it. Your price targets and ' +
      'volatility settings move across.', '');
    if (!next) return;
    try {
      const r = await api(`api/coins/${encodeURIComponent(sym)}/replace`,
        {method:'POST', body: JSON.stringify({symbol: next.trim()})});
      if (selected === sym) selected = r.symbol;
      await refresh();
      alert(`${sym} replaced by ${r.symbol} (${r.source}).`);
    } catch (e) { alert('Could not replace: ' + e.message); }
  });
  tb.querySelectorAll('[data-rm]').forEach(b => b.onclick = async () => {
    const sym = b.dataset.rm;
    const n = state.targets.filter(t => t.symbol === sym).length;
    const warn = n ? `\n\nThis also deletes ${n} price target${n > 1 ? 's' : ''}.` : '';
    if (!confirm(`Stop tracking ${sym}?${warn}`)) return;
    try {
      await api('api/coins/' + encodeURIComponent(sym), {method:'DELETE'});
      if (selected === sym) selected = null;
      await refresh();
    } catch (e) { alert('Could not remove: ' + e.message); }
  });
}

/* ---------- fluctuation ---------- */
function renderFluct() {
  const tb = document.querySelector('#fluctTable tbody');
  tb.innerHTML = state.coins.map(c => {
    const f = c.fluctuation || {};
    return `<tr data-sym="${c.symbol}">
      <td><span class="cellCoin">${coinMark(c.ticker, 24)}<b>${esc(c.ticker)}</b></span></td>
      <td><input class="mini" type="number" step="0.5" min="0" data-f="pct" value="${f.pct ?? 5}">&nbsp;%</td>
      <td><select data-f="window_s">
            ${[300,900,1800,3600,7200,14400].map(v=>`<option value="${v}" ${f.window_s==v?'selected':''}>${humanDur(v)}</option>`).join('')}
          </select></td>
      <td><input class="mini" type="number" step="0.5" min="0" data-f="urgent_pct" value="${f.urgent_pct ?? 10}">&nbsp;%</td>
      <td><select data-f="urgent_repeat">
            ${[1,2,3,4,5].map(v=>`<option value="${v}" ${(f.urgent_repeat ?? 3)==v?'selected':''}>×${v}</option>`).join('')}
          </select></td>
      <td><input type="checkbox" data-f="enabled" ${f.enabled?'checked':''}></td>
    </tr>`;
  }).join('');
  tb.querySelectorAll('tr').forEach(tr => {
    tr.querySelectorAll('[data-f]').forEach(inp => {
      inp.onchange = async () => {
        const body = {symbol: tr.dataset.sym};
        body[inp.dataset.f] = inp.type === 'checkbox' ? (inp.checked?1:0) : inp.value;
        try { await api('api/fluctuation', {method:'POST', body:JSON.stringify(body)}); }
        catch (e) { alert('Could not save: ' + e.message); }
      };
    });
  });
}

/* ---------- events ---------- */
function renderEvents() {
  const box = document.getElementById('eventList');
  if (!state.events.length) { box.innerHTML = '<p class="empty">Nothing yet.</p>'; return; }
  const ico = {target:'🎯', fluctuation:'⚡', summary:'📊', system:'⚙️'};
  box.innerHTML = state.events.map(e => `
    <div class="ev ${esc(e.kind)}">
      <span class="ico">${ico[e.kind]||'•'}</span>
      <div class="body">
        <div class="txt">${esc(stripTags(e.message))}</div>
        <div class="meta">
          <span>${new Date(e.ts*1000).toLocaleString()}</span>
          <span>${ago(e.ts)}</span>
          ${e.sent ? '<span>✓ sent</span>'
                   : `<span class="fail">✗ not sent${e.error?' — '+esc(e.error):''}</span>`}
        </div>
      </div>
    </div>`).join('');
}

/* ---------- settings ---------- */
function renderSettings() {
  const s = state.settings || {};
  const tok = document.getElementById('tgToken');
  if (document.activeElement !== tok) {
    tok.value = '';
    tok.placeholder = s.tg_token_set ? s.tg_token : '123456:ABC-DEF…';
  }
  document.getElementById('tgChat').value = s.tg_chat_id || '';
  document.getElementById('tgEnabled').checked = s.tg_enabled === '1';
  document.getElementById('tgQuiet').value = s.quiet_hours || '';
  document.getElementById('tgSummary').checked = s.summary_enabled === '1';
  document.getElementById('tgSummaryHour').value = s.summary_hour || '9';
}
function renderSymbolOptions() {
  const sel = document.getElementById('tSymbol');
  const cur = sel.value;
  sel.innerHTML = state.coins.map(c =>
    `<option value="${c.symbol}">${esc(c.ticker)} — ${esc(c.name)}</option>`).join('');
  if (cur) sel.value = cur;
}
function renderFeed() {
  const f = state.feed || {};
  const dot = document.getElementById('feedDot');
  const txt = document.getElementById('feedText');
  dot.className = 'dot ' + (f.connected ? 'on' : 'off');
  const srcs = (f.sources || []).join(' + ');
  txt.textContent = f.connected ? ('live · ' + (srcs || 'no coins'))
    : (srcs ? srcs : (f.error ? 'reconnecting — ' + f.error : 'disconnected'));
}

function renderAll() {
  renderCoins(); renderCoinManager(); renderTargets(); renderFluct();
  renderEvents(); renderSettings(); renderSymbolOptions(); renderFeed();
  updateChartHead();
}

async function refresh() {
  try {
    state = await api('api/state');
    if (state.icons) iconSet = new Set(state.icons);
    renderAll();
  } catch (e) {
    document.getElementById('feedText').textContent = 'server unreachable';
    document.getElementById('feedDot').className = 'dot off';
  }
}

/* ---------- live stream ---------- */
function connectStream() {
  const es = new EventSource('api/stream');
  es.onmessage = ev => {
    let m; try { m = JSON.parse(ev.data); } catch { return; }
    if (m.type === 'prices') {
      for (const [sym, d] of Object.entries(m.prices)) updatePrice(sym, d.price, d.change24h);
    } else if (m.type === 'status') {
      state.feed = {connected:m.connected, error:m.error};
      renderFeed();
    }
  };
  es.onerror = () => {
    document.getElementById('feedDot').className = 'dot off';
    es.close();
    setTimeout(connectStream, 4000);      // EventSource auto-retry is not enough behind a proxy
  };
}

/* ---------- wiring ---------- */
function showTab(name) {
  const tab = document.querySelector(`.tab[data-tab="${name}"]`);
  if (!tab) return;
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
  tab.classList.add('active');
  document.getElementById(name).classList.add('active');
  if (name === 'overview' && chart) setTimeout(() => chart.timeScale().fitContent(), 30);
  // The hash matches the panel's id, so the browser scrolls it into view and
  // pushes the header off-screen. Undo that.
  window.scrollTo(0, 0);
}
document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
  showTab(t.dataset.tab);
  history.replaceState(null, '', '#' + t.dataset.tab);   // bookmarkable, survives reload
});
window.addEventListener('hashchange', () => showTab(location.hash.slice(1) || 'overview'));
if (location.hash) showTab(location.hash.slice(1));

document.getElementById('ranges').onclick = e => {
  if (e.target.tagName !== 'BUTTON') return;
  document.querySelectorAll('#ranges button').forEach(b => b.classList.remove('on'));
  e.target.classList.add('on');
  rangeMin = +e.target.dataset.min;
  loadChart();
};

document.getElementById('targetForm').onsubmit = async e => {
  e.preventDefault();
  const msg = document.getElementById('targetMsg');
  try {
    const r = await api('api/targets', {method:'POST', body: JSON.stringify({
      symbol: document.getElementById('tSymbol').value,
      direction: document.getElementById('tDirection').value,
      price: parseFloat(document.getElementById('tPrice').value),
      note: document.getElementById('tNote').value,
      repeat: document.getElementById('tRepeat').checked ? 1 : 0,
      cooldown_s: +document.getElementById('tCooldown').value,
    })});
    flash(msg, r.armed ? 'Target added and armed.'
      : 'Target added. The price is already past this level, so it will arm once the price moves back and fire on a real crossing.', true);
    document.getElementById('tPrice').value = '';
    document.getElementById('tNote').value = '';
    refresh();
  } catch (err) { flash(msg, err.message, false); }
};

document.getElementById('coinForm').onsubmit = async e => {
  e.preventDefault();
  const msg = document.getElementById('coinMsg');
  const f = document.getElementById('cSymbol');
  try {
    const nameEl = document.getElementById('cName');
    const r = await api('api/coins', {method:'POST', body: JSON.stringify({
      symbol: f.value.trim(), name: (nameEl.value || '').trim() || undefined,
    })});
    flash(msg, `${r.symbol} added — backfilling history and joining the live stream.`, true);
    f.value = ''; nameEl.value = '';
    refresh();
  } catch (err) { flash(msg, err.message, false); }
};

document.getElementById('tgForm').onsubmit = async e => {
  e.preventDefault();
  const msg = document.getElementById('tgMsg');
  try {
    await api('api/settings', {method:'POST', body: JSON.stringify({
      tg_token: document.getElementById('tgToken').value,
      tg_chat_id: document.getElementById('tgChat').value.trim(),
      tg_enabled: document.getElementById('tgEnabled').checked ? '1':'0',
      quiet_hours: document.getElementById('tgQuiet').value.trim(),
      summary_enabled: document.getElementById('tgSummary').checked ? '1':'0',
      summary_hour: document.getElementById('tgSummaryHour').value,
    })});
    flash(msg, 'Saved.', true); refresh();
  } catch (err) { flash(msg, err.message, false); }
};

document.getElementById('tgTest').onclick = async () => {
  const msg = document.getElementById('tgMsg');
  flash(msg, 'Sending…', true);
  try {
    const r = await api('api/telegram/test', {method:'POST', body: JSON.stringify({
      tg_token: document.getElementById('tgToken').value,
      tg_chat_id: document.getElementById('tgChat').value.trim(),
    })});
    r.ok ? flash(msg, `Sent — check Telegram (bot @${r.bot}).`, true)
         : flash(msg, r.error, false);
    refresh();
  } catch (e) { flash(msg, e.message, false); }
};

document.getElementById('tgDetect').onclick = async () => {
  const msg = document.getElementById('tgMsg'), box = document.getElementById('tgChats');
  flash(msg, 'Looking for recent chats…', true);
  box.innerHTML = '';
  try {
    const r = await api('api/telegram/detect', {method:'POST', body: JSON.stringify({
      tg_token: document.getElementById('tgToken').value,
    })});
    if (!r.ok) return flash(msg, r.error, false);
    if (!r.chats.length)
      return flash(msg, 'No chats found. Send your bot a message first, then try again.', false);
    flash(msg, 'Click the chat to use it:', true);
    box.innerHTML = r.chats.map(c =>
      `<div class="chatChip" data-id="${esc(c.id)}">${esc(c.name)}<b>${esc(c.id)}</b></div>`).join('');
    box.querySelectorAll('.chatChip').forEach(el => el.onclick = () => {
      document.getElementById('tgChat').value = el.dataset.id;
      box.innerHTML = '';
      flash(msg, 'Chat selected — press Save.', true);
    });
  } catch (e) { flash(msg, e.message, false); }
};

document.getElementById('tgSummaryNow').onclick = async () => {
  const msg = document.getElementById('tgMsg');
  try {
    const r = await api('api/summary/send', {method:'POST', body:'{}'});
    flash(msg, r.ok ? 'Summary sent.' : 'No price data yet.', r.ok);
    refresh();
  } catch (e) { flash(msg, e.message, false); }
};

// ?static=1 skips the live stream. An open SSE connection means a headless
// browser never reaches network-idle, so screenshots and previews hang.
const STATIC = new URLSearchParams(location.search).has('static');

refresh();
if (!STATIC) connectStream();
if (!STATIC) {
  setInterval(refresh, 30000);
  setInterval(() => { if (selected) loadChart(); }, 60000);
}
