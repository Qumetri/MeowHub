"use strict";
/* Crypto tracker UI. No framework: one state object, render on change,
   live prices pushed over SSE.
   Source of web/app.js: edit here, then `npm run build` in web-src/ — never edit the emitted file. */
/* ---------- DOM helpers ---------- */
/** getElementById for ids that index.html always has. */
function byId(id) {
    const el = document.getElementById(id);
    if (!el)
        throw new Error('missing #' + id);
    return el;
}
const input = (id) => byId(id);
const errMsg = (e) => (e instanceof Error ? e.message : String(e));
const COLORS = {
    BTC: '#f7931a', ETH: '#627eea', ETC: '#3ab83a', RVN: '#6b73b8',
    TON: '#0098ea', TRX: '#eb0029', SOL: '#14f195', XMR: '#ff6600',
};
const colorFor = (t) => COLORS[t] || '#7928ca';
let state = { coins: [], targets: [], settings: {}, events: [], feed: {}, icons: [] };
// The demo page's "Log in" link lands on ./?login (it makes Caddy ask for the password); tidy the URL.
if (/[?&]login\b/.test(location.search))
    history.replaceState(null, '', location.pathname + location.hash);
let iconSet = new Set();
let selected = null, rangeMin = 1440;
let chart = null;
let series = null;
const lastPx = {};
function fmtPrice(p) {
    if (p === null || p === undefined || !isFinite(p))
        return '—';
    const a = Math.abs(p);
    let s;
    if (a >= 1000)
        s = p.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    else if (a >= 1)
        s = p.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
    else if (a >= 0.01)
        s = p.toFixed(5).replace(/0+$/, '').replace(/\.$/, '');
    else if (a >= 0.0001)
        s = p.toFixed(7).replace(/0+$/, '').replace(/\.$/, '');
    else
        s = p.toFixed(9).replace(/0+$/, '').replace(/\.$/, '');
    return s;
}
const fmtPct = (x) => (x === null || x === undefined || !isFinite(x)) ? '—'
    : (x >= 0 ? '+' : '') + x.toFixed(2) + '%';
const cls = (x) => (x === null || x === undefined || !isFinite(x)) ? 'flat' : (x > 0 ? 'up' : x < 0 ? 'dn' : 'flat');
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' };
const esc = (s) => String(s ?? '').replace(/[&<>"]/g, c => ESC[c] ?? c);
const stripTags = (s) => String(s ?? '').replace(/<[^>]*>/g, '');
function humanDur(sec) {
    const s = Number(sec) || 0;
    if (!s)
        return 'none';
    if (s < 60)
        return s + 's';
    if (s < 3600)
        return Math.round(s / 60) + ' min';
    return Math.round(s / 3600) + 'h';
}
function ago(ts) {
    const d = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (d < 60)
        return d + 's ago';
    if (d < 3600)
        return Math.floor(d / 60) + 'm ago';
    if (d < 86400)
        return Math.floor(d / 3600) + 'h ago';
    return Math.floor(d / 86400) + 'd ago';
}
// '15-minute' etc. from the spacing of the first two candles (the server sends one-minute ones).
function candleWord(rows) {
    const [a, b] = rows;
    const m = a && b ? Math.round((b.time - a.time) / 60) : 1;
    return m <= 1 ? 'one-minute' : m % 60 ? `${m}-minute` : m === 60 ? 'one-hour' : `${m / 60}-hour`;
}
async function api(path, opts) {
    const r = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, opts));
    const t = await r.text();
    let j = {};
    try {
        j = t ? JSON.parse(t) : {};
    }
    catch {
        throw new Error('bad response from server');
    }
    if (!r.ok)
        throw new Error(j.error || ('HTTP ' + r.status));
    return j;
}
function flash(el, txt, ok) {
    el.textContent = txt;
    el.className = 'msg ' + (ok ? 'ok' : 'bad');
    if (ok)
        setTimeout(() => { if (el.textContent === txt)
            el.textContent = ''; }, 5000);
}
/* ---------- coin mark ---------- */
// A vendored logo when we have one, otherwise a coloured ticker badge. The
// fallback matters: a newly listed or renamed coin (GRAM) predates the icon
// pack, and a broken image would be worse than no logo at all.
function coinMark(ticker, size = 28) {
    const t = String(ticker || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const label = esc(String(ticker || '?').slice(0, 4));
    const has = iconSet.has(String(ticker || '').toUpperCase());
    return `<span class="mark" style="width:${size}px;height:${size}px;background:${colorFor(ticker ?? '')}">`
        + `<span class="markTxt" style="font-size:${Math.max(8, Math.round(size * 0.34))}px">${label}</span>`
        + (has ? `<img src="icons/${t}.svg" alt="" loading="lazy" onerror="this.remove()">` : '')
        + `</span>`;
}
const isHalted = (c) => !!c.health && c.health !== 'ok';
/* ---------- coin cards ---------- */
function renderCoins() {
    const g = byId('coinGrid');
    g.innerHTML = state.coins.map(c => `
    <div class="coin ${selected === c.symbol ? 'sel' : ''} ${isHalted(c) ? 'bad' : ''}" data-sym="${c.symbol}">
      <span class="bar" style="background:${colorFor(c.ticker)}"></span>
      <div class="head">
        ${coinMark(c.ticker, 30)}
        <span class="nm"><b>${esc(c.ticker)}</b><br>${esc(c.name)}</span>
      </div>
      <div class="px" id="px-${c.symbol}">${c.price != null ? '$' + fmtPrice(c.price) : '—'}</div>
      ${isHalted(c)
        ? `<span class="chg dn" title="${esc(c.health_note)}">⚠ not live</span>`
        : `<span class="chg ${cls(c.change24h)}" id="ch-${c.symbol}">${fmtPct(c.change24h)}</span>`}
    </div>`).join('');
    g.querySelectorAll('.coin').forEach(el => el.onclick = () => selectCoin(el.dataset.sym ?? ''));
    const first = state.coins[0];
    if (!selected && first)
        selectCoin(first.symbol);
}
function updatePrice(sym, price, ch) {
    const pe = document.getElementById('px-' + sym);
    if (pe) {
        const prev = lastPx[sym];
        pe.textContent = '$' + fmtPrice(price);
        if (prev != null && price !== prev) {
            pe.classList.remove('flash-up', 'flash-dn');
            void pe.offsetWidth; // restart the transition
            pe.classList.add(price > prev ? 'flash-up' : 'flash-dn');
            setTimeout(() => pe.classList.remove('flash-up', 'flash-dn'), 700);
        }
    }
    lastPx[sym] = price;
    const ce = document.getElementById('ch-' + sym);
    if (ce && ch != null) {
        ce.textContent = fmtPct(ch);
        ce.className = 'chg ' + cls(ch);
    }
    const c = state.coins.find(x => x.symbol === sym);
    if (c) {
        c.price = price;
        if (ch != null)
            c.change24h = ch;
    }
    if (sym === selected)
        updateChartHead();
    renderTargetDistances();
    const row = document.querySelector(`#coinTable tr[data-sym="${sym}"]`);
    if (row) {
        const [, , pxCell, chCell] = row.querySelectorAll('td');
        if (pxCell)
            pxCell.textContent = '$' + fmtPrice(price);
        if (ch != null && chCell) {
            chCell.textContent = fmtPct(ch);
            chCell.style.color = ch > 0 ? 'var(--up)' : ch < 0 ? 'var(--down)' : 'var(--muted)';
        }
    }
}
/* ---------- chart ---------- */
function ensureChart() {
    if (chart && series)
        return series;
    chart = LightweightCharts.createChart(byId('chart'), {
        layout: { background: { type: LightweightCharts.ColorType.Solid, color: 'transparent' }, textColor: '#8b93a8', fontFamily: 'JetBrains Mono, monospace' },
        grid: { vertLines: { color: 'rgba(255,255,255,.04)' }, horzLines: { color: 'rgba(255,255,255,.04)' } },
        rightPriceScale: { borderColor: 'rgba(255,255,255,.08)' },
        timeScale: { borderColor: 'rgba(255,255,255,.08)', timeVisible: true, secondsVisible: false },
        crosshair: { mode: LightweightCharts.CrosshairMode.Magnet },
        localization: { priceFormatter: (p) => '$' + fmtPrice(p) },
        autoSize: true,
    });
    series = chart.addCandlestickSeries({
        upColor: '#34d399', downColor: '#f87171', borderVisible: false,
        wickUpColor: '#34d399', wickDownColor: '#f87171',
    });
    return series;
}
async function loadChart() {
    if (!selected)
        return;
    const s = ensureChart();
    try {
        const j = await api(`api/candles?symbol=${encodeURIComponent(selected)}&minutes=${rangeMin}`);
        const rows = (j.candles || []).map(c => ({ time: c.ts, open: c.o, high: c.h, low: c.l, close: c.c }));
        s.setData(rows);
        // Price needs enough decimals for RVN without wrecking BTC.
        const last = rows.at(-1)?.close ?? 1;
        const dec = last >= 1000 ? 2 : last >= 1 ? 4 : last >= 0.01 ? 5 : 7;
        s.applyOptions({ priceFormat: { type: 'price', precision: dec, minMove: Math.pow(10, -dec) } });
        chart?.timeScale().fitContent();
        byId('chartHint').textContent =
            rows.length ? `${rows.length} ${candleWord(rows)} candles` : 'No data yet — backfilling from Binance.';
    }
    catch (e) {
        byId('chartHint').textContent = 'Chart failed: ' + errMsg(e);
    }
}
function updateChartHead() {
    const c = state.coins.find(x => x.symbol === selected);
    if (!c)
        return;
    byId('chartTitle').innerHTML =
        `${coinMark(c.ticker, 32)}<span>${esc(c.name)}</span>`;
    const bits = [`$${fmtPrice(c.price)}`, `24h ${fmtPct(c.change24h)}`];
    if (c.high)
        bits.push(`H $${fmtPrice(c.high)}`);
    if (c.low)
        bits.push(`L $${fmtPrice(c.low)}`);
    byId('chartSub').textContent = bits.join('  ·  ');
}
function selectCoin(sym) {
    selected = sym;
    document.querySelectorAll('.coin').forEach(e => e.classList.toggle('sel', e.dataset.sym === sym));
    updateChartHead();
    void loadChart();
}
/* ---------- targets ---------- */
function renderTargets() {
    const tb = document.querySelector('#targetTable tbody');
    const rows = state.targets;
    byId('noTargets').style.display = rows.length ? 'none' : 'block';
    tb.innerHTML = rows.map(t => {
        const c = state.coins.find(x => x.symbol === t.symbol) || { ticker: t.symbol };
        const up = t.direction === 'above';
        const stateLbl = !t.enabled ? '<span class="pill off">done</span>'
            : t.armed ? '<span class="pill armed">armed</span>'
                : '<span class="pill waiting">waiting</span>';
        return `<tr data-id="${t.id}">
      <td><span class="cellCoin">${coinMark(c.ticker, 24)}<b>${esc(c.ticker)}</b></span></td>
      <td class="cond"><span class="${up ? 'up' : 'dn'}">${up ? '▲' : '▼'}</span> $${fmtPrice(t.price)}
          ${t.repeat ? '<span class="pill">repeat</span>' : ''} ${stateLbl}</td>
      <td class="num dist" id="dist-${t.id}">—</td>
      <td>${esc(t.note) || '<span style="color:var(--muted)">—</span>'}</td>
      <td class="num">${t.fire_count || 0}</td>
      <td style="text-align:right"><button class="iconbtn" data-del="${t.id}" title="Delete">✕</button></td>
    </tr>`;
    }).join('');
    tb.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
        if (!confirm('Delete this target?'))
            return;
        await api('api/targets/' + b.dataset.del, { method: 'DELETE' });
        void refresh();
    });
    renderTargetDistances();
}
function renderTargetDistances() {
    state.targets.forEach(t => {
        const el = document.getElementById('dist-' + t.id);
        if (!el)
            return;
        const c = state.coins.find(x => x.symbol === t.symbol);
        if (!c || c.price == null) {
            el.textContent = '—';
            return;
        }
        const d = (t.price - c.price) / c.price * 100;
        el.textContent = fmtPct(d);
        el.style.color = Math.abs(d) < 1 ? 'var(--fg)' : 'var(--muted)';
    });
}
/* ---------- coin management ---------- */
const chgColor = (x) => x != null && x > 0 ? 'var(--up)' : x != null && x < 0 ? 'var(--down)' : 'var(--muted)';
function renderCoinManager() {
    const tb = document.querySelector('#coinTable tbody');
    if (!tb)
        return;
    tb.innerHTML = state.coins.map(c => {
        const n = state.targets.filter(t => t.symbol === c.symbol).length;
        return `<tr data-sym="${c.symbol}">
      <td><span class="cellCoin">${coinMark(c.ticker, 26)}
          <span><b>${esc(c.ticker)}</b> <span class="nm">${esc(c.name)}</span></span></span></td>
      <td class="num" style="color:var(--muted)">${esc(c.symbol)}
          <span class="pill ${c.source === 'kraken' ? 'kraken' : ''}">${esc(c.source || 'binance')}</span></td>
      <td class="num">${c.price != null ? '$' + fmtPrice(c.price) : '—'}</td>
      <td class="num" style="color:${chgColor(c.change24h)}">${fmtPct(c.change24h)}</td>
      <td>${isHalted(c)
            ? `<span class="pill halted" title="${esc(c.health_note)}">${esc(c.health)}</span>`
            : '<span class="pill ok">live</span>'}</td>
      <td class="num">${n || '<span style="color:var(--muted)">0</span>'}</td>
      <td style="text-align:right">
        ${isHalted(c)
            ? `<button class="iconbtn swap" data-swap="${c.symbol}" title="Replace with its successor, keeping targets">⇄</button>`
            : ''}
        <button class="iconbtn" data-rm="${c.symbol}" title="Stop tracking">✕</button></td>
    </tr>`;
    }).join('');
    tb.querySelectorAll('[data-swap]').forEach(b => b.onclick = async () => {
        const sym = b.dataset.swap ?? '';
        const c = state.coins.find(x => x.symbol === sym);
        const next = prompt(`${sym} is no longer trading (${c?.health_note || 'halted'}).\n\n` +
            'Enter the ticker or symbol that replaced it. Your price targets and ' +
            'volatility settings move across.', '');
        if (!next)
            return;
        try {
            const r = await api(`api/coins/${encodeURIComponent(sym)}/replace`, { method: 'POST', body: JSON.stringify({ symbol: next.trim() }) });
            if (selected === sym)
                selected = r.symbol;
            await refresh();
            alert(`${sym} replaced by ${r.symbol} (${r.source}).`);
        }
        catch (e) {
            alert('Could not replace: ' + errMsg(e));
        }
    });
    tb.querySelectorAll('[data-rm]').forEach(b => b.onclick = async () => {
        const sym = b.dataset.rm ?? '';
        const n = state.targets.filter(t => t.symbol === sym).length;
        const warn = n ? `\n\nThis also deletes ${n} price target${n > 1 ? 's' : ''}.` : '';
        if (!confirm(`Stop tracking ${sym}?${warn}`))
            return;
        try {
            await api('api/coins/' + encodeURIComponent(sym), { method: 'DELETE' });
            if (selected === sym)
                selected = null;
            await refresh();
        }
        catch (e) {
            alert('Could not remove: ' + errMsg(e));
        }
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
            ${[300, 900, 1800, 3600, 7200, 14400].map(v => `<option value="${v}" ${f.window_s == v ? 'selected' : ''}>${humanDur(v)}</option>`).join('')}
          </select></td>
      <td><input class="mini" type="number" step="0.5" min="0" data-f="urgent_pct" value="${f.urgent_pct ?? 10}">&nbsp;%</td>
      <td><select data-f="urgent_repeat">
            ${[1, 2, 3, 4, 5].map(v => `<option value="${v}" ${(f.urgent_repeat ?? 3) == v ? 'selected' : ''}>×${v}</option>`).join('')}
          </select></td>
      <td><input type="checkbox" data-f="enabled" ${f.enabled ? 'checked' : ''}></td>
    </tr>`;
    }).join('');
    tb.querySelectorAll('tr').forEach(tr => {
        tr.querySelectorAll('[data-f]').forEach(inp => {
            inp.onchange = async () => {
                const body = { symbol: tr.dataset.sym ?? '' };
                body[inp.dataset.f ?? ''] = inp instanceof HTMLInputElement && inp.type === 'checkbox' ? (inp.checked ? 1 : 0) : inp.value;
                try {
                    await api('api/fluctuation', { method: 'POST', body: JSON.stringify(body) });
                }
                catch (e) {
                    alert('Could not save: ' + errMsg(e));
                }
            };
        });
    });
}
/* ---------- events ---------- */
const EVENT_ICON = { target: '🎯', fluctuation: '⚡', summary: '📊', system: '⚙️', news: '📰' };
function renderEvents() {
    const box = byId('eventList');
    if (!state.events.length) {
        box.innerHTML = '<p class="empty">Nothing yet.</p>';
        return;
    }
    box.innerHTML = state.events.map(e => `
    <div class="ev ${esc(e.kind)}">
      <span class="ico">${EVENT_ICON[e.kind] || '•'}</span>
      <div class="body">
        <div class="txt">${esc(stripTags(e.message))}</div>
        <div class="meta">
          <span>${new Date(e.ts * 1000).toLocaleString()}</span>
          <span>${ago(e.ts)}</span>
          ${e.sent ? '<span>✓ sent</span>'
        : `<span class="fail">✗ not sent${e.error ? ' — ' + esc(e.error) : ''}</span>`}
        </div>
      </div>
    </div>`).join('');
}
/* ---------- settings ---------- */
const TOKEN_SOURCE = { override: 'Token set on the MeowHub Bots page',
    env: 'Token from .env', ui: 'Token saved here' };
function renderSettings() {
    const s = state.settings || {};
    const tok = input('tgToken');
    // Set in .env (CRYPTO_TG_TOKEN / CRYPTO_TG_CHAT_ID): shown, not editable here.
    tok.disabled = !!s.tg_token_env || s.tg_token_source === 'override';
    if (document.activeElement !== tok) {
        tok.value = '';
        tok.placeholder = s.tg_token_env ? `${s.tg_token}  (set in .env: CRYPTO_TG_TOKEN)`
            : s.tg_token_set ? s.tg_token ?? '' : '123456:ABC-DEF…';
    }
    const srcNote = document.getElementById('tgTokenSrc');
    if (srcNote)
        srcNote.textContent = TOKEN_SOURCE[s.tg_token_source ?? ''] || '';
    const chat = input('tgChat');
    chat.disabled = !!s.tg_chat_env;
    chat.title = s.tg_chat_env ? 'Set in .env: CRYPTO_TG_CHAT_ID' : '';
    chat.value = s.tg_chat_id || '';
    input('tgEnabled').checked = s.tg_enabled === '1';
    input('tgQuiet').value = s.quiet_hours || '';
    input('tgSummary').checked = s.summary_enabled === '1';
    input('tgSummaryHour').value = s.summary_hour || '9';
    renderNewsSettings();
}
/* ---------- news (newswatch) ---------- */
let newsRows = [], newsHealth = null;
const fmtLag = (s) => (s === null || s === undefined) ? '—'
    : s < 90 ? Math.round(s) + ' s' : s < 5400 ? Math.round(s / 60) + ' min' : (s / 3600).toFixed(1) + ' h';
const agoRu = (ts) => {
    if (!ts)
        return '—';
    const d = Math.max(0, Math.floor(Date.now() / 1000 - ts));
    if (d < 90)
        return d + ' s ago';
    if (d < 5400)
        return Math.round(d / 60) + ' min ago';
    if (d < 172800)
        return Math.round(d / 3600) + ' h ago';
    return Math.round(d / 86400) + ' d ago';
};
const safeUrl = (u) => /^https?:\/\//.test(u || '') ? u ?? '#' : '#';
function renderNewsSettings() {
    const s = state.settings || {};
    const put = (id, f) => {
        const el = document.getElementById(id);
        if (el instanceof HTMLInputElement && document.activeElement !== el)
            f(el);
    };
    put('nwEnabled', el => { el.checked = s.news_enabled !== '0'; });
    put('nwMin', el => { el.value = s.news_min_score ?? '70'; });
    put('nwMax', el => { el.value = s.news_max_per_day ?? '10'; });
    put('nwChannels', el => { el.value = s.news_tg_channels ?? 'WatcherGuru'; });
}
function renderNews() {
    const tb = document.querySelector('#newsTable tbody');
    byId('noNews').style.display = newsRows.length ? 'none' : 'block';
    tb.innerHTML = newsRows.map(n => {
        const sc = n.score >= 90 ? 'halted' : n.score >= 70 ? 'waiting' : n.score >= 50 ? 'kraken' : '';
        const ts = n.published || n.first_seen;
        return `<tr class="${n.seeded ? 'seeded' : ''}">
      <td class="num" title="${esc(new Date(ts * 1000).toLocaleString())}">${agoRu(ts)}</td>
      <td>${n.coins.length ? n.coins.map(t => `<span class="cellCoin">${coinMark(t, 20)}<b>${esc(t)}</b></span>`).join(' ')
            : '<span style="color:var(--muted)">—</span>'}</td>
      <td>${esc(n.label)}</td>
      <td><span class="pill ${sc}">${n.score}</span></td>
      <td class="newsTitle"><a href="${esc(safeUrl(n.url))}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a></td>
      <td>${n.pushed ? '<span class="pill ok">push</span>' : ''}</td>
    </tr>`;
    }).join('');
    const hb = document.querySelector('#newsHealth tbody');
    const hs = newsHealth?.sources ?? [];
    hb.innerHTML = hs.map(h => `<tr>
      <td>${esc(h.label)} <span class="nm" style="color:var(--muted)">${esc(h.source)}</span></td>
      <td class="num">${h.interval_s} s</td>
      <td class="num">${agoRu(h.last_ok)}</td>
      <td class="num">${h.consecutive_errors
        ? `<span class="pill halted" title="${esc(h.last_error)}">${h.consecutive_errors}</span>` : '0'}</td>
      <td class="num">${agoRu(h.last_new_item)}</td>
      <td class="num">${h.items_7d}</td>
      <td class="num">${fmtLag(h.latency_p50_s)} / ${fmtLag(h.latency_p90_s)}</td>
    </tr>`).join('');
}
async function loadNews() {
    try {
        const [n, h] = await Promise.all([api('api/news?limit=50'), api('api/news/health')]);
        newsRows = n.news || [];
        newsHealth = h;
        renderNews();
    }
    catch { /* the tab keeps showing the last good copy */ }
}
function renderSymbolOptions() {
    const sel = byId('tSymbol');
    const cur = sel.value;
    sel.innerHTML = state.coins.map(c => `<option value="${c.symbol}">${esc(c.ticker)} — ${esc(c.name)}</option>`).join('');
    if (cur)
        sel.value = cur;
}
function renderFeed() {
    const f = state.feed || {};
    const dot = byId('feedDot');
    const txt = byId('feedText');
    dot.className = 'dot ' + (f.connected ? 'on' : 'off');
    const srcs = (f.sources || []).join(' + ');
    txt.textContent = f.connected ? ('live · ' + (srcs || 'no coins'))
        : (srcs ? srcs : (f.error ? 'reconnecting — ' + f.error : 'disconnected'));
}
function renderAll() {
    renderCoins();
    renderCoinManager();
    renderTargets();
    renderFluct();
    renderEvents();
    renderSettings();
    renderSymbolOptions();
    renderFeed();
    updateChartHead();
}
async function refresh() {
    try {
        state = await api('api/state');
        if (state.icons)
            iconSet = new Set(state.icons);
        renderAll();
        if (byId('news').classList.contains('active'))
            void loadNews();
    }
    catch {
        byId('feedText').textContent = 'server unreachable';
        byId('feedDot').className = 'dot off';
    }
}
/* ---------- live stream ---------- */
function connectStream() {
    const es = new EventSource('api/stream');
    es.onmessage = (ev) => {
        let m;
        try {
            m = JSON.parse(ev.data);
        }
        catch {
            return;
        }
        if (m.type === 'prices') {
            for (const [sym, d] of Object.entries(m.prices))
                updatePrice(sym, d.price, d.change24h);
        }
        else if (m.type === 'status') {
            state.feed = Object.assign({}, state.feed, { connected: m.connected, error: m.error }); // keep `sources`
            renderFeed();
        }
    };
    es.onerror = () => {
        byId('feedDot').className = 'dot off';
        es.close();
        setTimeout(connectStream, 4000); // EventSource auto-retry is not enough behind a proxy
    };
}
/* ---------- wiring ---------- */
function showTab(name) {
    const tab = document.querySelector(`.tab[data-tab="${name}"]`);
    if (!tab)
        return;
    document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
    tab.classList.add('active');
    byId(name).classList.add('active');
    if (name === 'overview' && chart)
        setTimeout(() => chart?.timeScale().fitContent(), 30);
    if (name === 'news')
        void loadNews();
    // The hash matches the panel's id, so the browser scrolls it into view and
    // pushes the header off-screen. Undo that.
    window.scrollTo(0, 0);
}
document.querySelectorAll('.tab').forEach(t => t.onclick = () => {
    showTab(t.dataset.tab ?? '');
    history.replaceState(null, '', '#' + t.dataset.tab); // bookmarkable, survives reload
});
window.addEventListener('hashchange', () => showTab(location.hash.slice(1) || 'overview'));
if (location.hash)
    showTab(location.hash.slice(1));
byId('ranges').onclick = e => {
    const b = e.target;
    if (!(b instanceof HTMLButtonElement))
        return;
    document.querySelectorAll('#ranges button').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    rangeMin = +(b.dataset.min ?? 1440);
    void loadChart();
};
byId('targetForm').onsubmit = async (e) => {
    e.preventDefault();
    const msg = byId('targetMsg');
    try {
        const r = await api('api/targets', { method: 'POST', body: JSON.stringify({
                symbol: byId('tSymbol').value,
                direction: byId('tDirection').value,
                price: parseFloat(input('tPrice').value),
                note: input('tNote').value,
                repeat: input('tRepeat').checked ? 1 : 0,
                cooldown_s: +byId('tCooldown').value,
            }) });
        flash(msg, r.armed ? 'Target added and armed.'
            : 'Target added. The price is already past this level, so it will arm once the price moves back and fire on a real crossing.', true);
        input('tPrice').value = '';
        input('tNote').value = '';
        void refresh();
    }
    catch (err) {
        flash(msg, errMsg(err), false);
    }
};
byId('coinForm').onsubmit = async (e) => {
    e.preventDefault();
    const msg = byId('coinMsg');
    const f = input('cSymbol');
    try {
        const nameEl = input('cName');
        const r = await api('api/coins', { method: 'POST', body: JSON.stringify({
                symbol: f.value.trim(), name: (nameEl.value || '').trim() || undefined,
            }) });
        flash(msg, `${r.symbol} added — backfilling history and joining the live stream.`, true);
        f.value = '';
        nameEl.value = '';
        void refresh();
    }
    catch (err) {
        flash(msg, errMsg(err), false);
    }
};
byId('tgForm').onsubmit = async (e) => {
    e.preventDefault();
    const msg = byId('tgMsg');
    try {
        await api('api/settings', { method: 'POST', body: JSON.stringify({
                tg_token: input('tgToken').value,
                tg_chat_id: input('tgChat').value.trim(),
                tg_enabled: input('tgEnabled').checked ? '1' : '0',
                quiet_hours: input('tgQuiet').value.trim(),
                summary_enabled: input('tgSummary').checked ? '1' : '0',
                summary_hour: input('tgSummaryHour').value,
            }) });
        flash(msg, 'Saved.', true);
        void refresh();
    }
    catch (err) {
        flash(msg, errMsg(err), false);
    }
};
byId('newsForm').onsubmit = async (e) => {
    e.preventDefault();
    const msg = byId('newsMsg');
    try {
        await api('api/settings', { method: 'POST', body: JSON.stringify({
                news_enabled: input('nwEnabled').checked ? '1' : '0',
                news_min_score: input('nwMin').value,
                news_max_per_day: input('nwMax').value,
                news_tg_channels: input('nwChannels').value.trim(),
            }) });
        flash(msg, 'Saved.', true);
        await refresh();
        void loadNews();
    }
    catch (err) {
        flash(msg, errMsg(err), false);
    }
};
byId('tgTest').onclick = async () => {
    const msg = byId('tgMsg');
    flash(msg, 'Sending…', true);
    try {
        const r = await api('api/telegram/test', { method: 'POST', body: JSON.stringify({
                tg_token: input('tgToken').value,
                tg_chat_id: input('tgChat').value.trim(),
            }) });
        if (r.ok)
            flash(msg, `Sent — check Telegram (bot @${r.bot}).`, true);
        else
            flash(msg, r.error ?? 'failed', false);
        void refresh();
    }
    catch (e) {
        flash(msg, errMsg(e), false);
    }
};
byId('tgDetect').onclick = async () => {
    const msg = byId('tgMsg'), box = byId('tgChats');
    flash(msg, 'Looking for recent chats…', true);
    box.innerHTML = '';
    try {
        const r = await api('api/telegram/detect', { method: 'POST', body: JSON.stringify({
                tg_token: input('tgToken').value,
            }) });
        if (!r.ok)
            return flash(msg, r.error ?? 'failed', false);
        const chats = r.chats ?? [];
        if (!chats.length)
            return flash(msg, 'No chats found. Send your bot a message first, then try again.', false);
        flash(msg, 'Click the chat to use it:', true);
        box.innerHTML = chats.map(c => `<div class="chatChip" data-id="${esc(c.id)}">${esc(c.name)}<b>${esc(c.id)}</b></div>`).join('');
        box.querySelectorAll('.chatChip').forEach(el => el.onclick = () => {
            input('tgChat').value = el.dataset.id ?? '';
            box.innerHTML = '';
            flash(msg, 'Chat selected — press Save.', true);
        });
    }
    catch (e) {
        flash(msg, errMsg(e), false);
    }
};
byId('tgSummaryNow').onclick = async () => {
    const msg = byId('tgMsg');
    try {
        const r = await api('api/summary/send', { method: 'POST', body: '{}' });
        flash(msg, r.ok ? 'Summary sent.' : 'No price data yet.', r.ok);
        void refresh();
    }
    catch (e) {
        flash(msg, errMsg(e), false);
    }
};
// ?static=1 skips the live stream. An open SSE connection means a headless
// browser never reaches network-idle, so screenshots and previews hang.
const STATIC = new URLSearchParams(location.search).has('static');
void refresh();
if (!STATIC)
    connectStream();
if (!STATIC) {
    setInterval(refresh, 30000);
    setInterval(() => { if (selected)
        void loadChart(); }, 60000);
}
