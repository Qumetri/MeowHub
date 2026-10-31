"use strict";
(() => {
    const RU = /^(ru|uk|be|kk)\b/i.test(navigator.language || '');
    const T = RU ? {
        head: 'Демо.',
        note: 'Цены и графики живые с Binance, цели, уведомления и новости — примеры. Изменения не сохраняются.',
        login: 'Войти',
        locked: 'Демо-режим: чтобы что-то менять, войди',
    } : {
        head: 'Demo.',
        note: 'Prices and charts are live from Binance; targets, alerts and news are examples. Nothing is saved.',
        login: 'Log in',
        locked: 'Demo mode: log in to change things',
    };
    const BINANCE = 'https://api.binance.com/api/v3/';
    const fetchReal = window.fetch.bind(window);
    const COINS = [
        ['BTCUSDT', 'BTC', 'Bitcoin', 68000], ['ETHUSDT', 'ETH', 'Ethereum', 2600],
        ['SOLUSDT', 'SOL', 'Solana', 150], ['TRXUSDT', 'TRX', 'TRON', 0.25],
        ['ETCUSDT', 'ETC', 'Ethereum Classic', 18], ['RVNUSDT', 'RVN', 'Ravencoin', 0.012],
    ];
    const now = () => Math.floor(Date.now() / 1000);
    const t0 = now();
    // ---------------------------------------------------------------- live prices --
    const live = {}; // symbol -> {price, change24h, high, low, vol}
    for (const [s, , , p] of COINS)
        live[s] = { price: p, change24h: 0, high: p, low: p, vol: 0 };
    let liveOk = false;
    async function pullPrices() {
        try {
            const q = encodeURIComponent(JSON.stringify(COINS.map((c) => c[0])));
            const r = await fetchReal(`${BINANCE}ticker/24hr?symbols=${q}`, { cache: 'no-store' });
            if (!r.ok)
                throw new Error(String(r.status));
            for (const x of (await r.json())) {
                live[x.symbol] = { price: +x.lastPrice, change24h: +x.priceChangePercent, high: +x.highPrice,
                    low: +x.lowPrice, vol: +x.quoteVolume };
            }
            liveOk = true;
        }
        catch {
            liveOk = false;
        }
    }
    const firstPull = pullPrices();
    // ------------------------------------------------------------------ demo data --
    const fluct = (s) => ({ symbol: s, pct: 5, window_s: 3600, cooldown_s: 1800, enabled: 1, last_fired: 0,
        last_price: 0, urgent_pct: 10, urgent_repeat: 3, ep_dir: 0, ep_anchor: 0, ep_step: 0, ep_peak: 0, ep_pb: 0 });
    const round = (x, step) => Math.round(x / step) * step;
    function targets() {
        const px = (sym) => live[sym]?.price ?? 0;
        const b = px('BTCUSDT'), e = px('ETHUSDT'), s = px('SOLUSDT');
        return [
            { id: 1, symbol: 'BTCUSDT', direction: 'above', price: round(b * 1.06, 1000), note: RU ? 'фиксирую часть' : 'take some profit', enabled: 1, armed: 1, repeat: 0, fire_count: 0, cooldown_s: 900 },
            { id: 2, symbol: 'ETHUSDT', direction: 'below', price: round(e * 0.9, 50), note: RU ? 'докупить' : 'buy the dip', enabled: 1, armed: 1, repeat: 1, fire_count: 2, cooldown_s: 3600 },
            { id: 3, symbol: 'SOLUSDT', direction: 'above', price: round(s * 1.15, 5), note: '', enabled: 1, armed: 0, repeat: 0, fire_count: 0, cooldown_s: 900 },
        ];
    }
    const ev = (id, ago, kind, symbol, message, sent = 1) => ({ id, ts: t0 - ago, kind, symbol, message, price: null, sent, error: '' });
    const EVENTS = RU ? [
        ev(5, 1500, 'fluctuation', 'SOLUSDT', '⚡ SOL +5.2% за час — пример уведомления о резком движении'),
        ev(4, 7200, 'target', 'ETHUSDT', '🎯 ETH опустился ниже цели — пример уведомления о цели'),
        ev(3, 20000, 'news', 'ETCUSDT', '📰 ETC · Binance · пример новости о делистинге пары'),
        ev(2, 86000, 'summary', '', '📊 Утренняя сводка за сутки — пример'),
        ev(1, 90000, 'system', '', '⚙️ Подключено к Binance + Kraken'),
    ] : [
        ev(5, 1500, 'fluctuation', 'SOLUSDT', '⚡ SOL +5.2% in an hour — an example of a sharp-move alert'),
        ev(4, 7200, 'target', 'ETHUSDT', '🎯 ETH fell below your target — an example of a target alert'),
        ev(3, 20000, 'news', 'ETCUSDT', '📰 ETC · Binance · an example of a pair-removal notice'),
        ev(2, 86000, 'summary', '', '📊 Daily morning summary — example'),
        ev(1, 90000, 'system', '', '⚙️ Connected to Binance + Kraken'),
    ];
    const SETTINGS = { tg_token: '**********demo', tg_token_set: true, tg_token_env: false, tg_token_source: 'ui',
        tg_chat_env: false, tg_chat_id: '', tg_enabled: '1', summary_enabled: '1', summary_hour: '9', quiet_hours: '23-08',
        retention_days: '45', news_enabled: '1', news_min_score: '70', news_max_per_day: '10', news_tg_channels: 'WatcherGuru' };
    const news = (id, ago, label, coins, score, title, url, pushed = 0) => ({ id, source: label.toLowerCase(), ext_id: String(id),
        title, url, published: t0 - ago, first_seen: t0 - ago + 60, coins, score, cluster: id, pushed, seeded: 0,
        label: label + (RU ? ' · пример' : ' · example'), kind: 'exchange' });
    const NEWS = [
        news(6, 900, 'Binance', ['ETC'], 100, 'Notice of Removal of Spot Trading Pairs', 'https://www.binance.com/en/support/announcement', 1),
        news(5, 5400, 'OKX', ['SOL'], 60, 'OKX to support the SOL network upgrade', 'https://www.okx.com/help/section/announcements-latest-announcements'),
        news(4, 12000, 'Kraken', ['XMR'], 40, 'Funding gateway maintenance', 'https://status.kraken.com/'),
        news(3, 30000, 'Bybit', ['TRX'], 30, 'New listing: perpetual contract', 'https://announcements.bybit.com/'),
    ];
    const HEALTH = { enabled: true, generated: t0, pushed_24h: 1, sources: ['Binance', 'OKX', 'Bybit', 'Kraken', 'Upbit'].map((l, i) => ({
            source: l.toLowerCase(), label: l, kind: 'exchange', interval_s: [30, 60, 60, 120, 60][i], last_ok: t0 - 20 - i * 7,
            last_try: t0 - 20 - i * 7, consecutive_errors: 0, last_error: '', last_new_item: t0 - 3600 * (i + 1), items_7d: 12 - i * 2,
            latency_samples: 10, latency_p50_s: 90 + i * 30, latency_p90_s: 400 + i * 60
        })) };
    function state() {
        return {
            coins: COINS.map(([symbol, ticker, name]) => ({ symbol, ticker, name, source: 'binance', health: 'ok', health_note: '',
                ...live[symbol], fluctuation: fluct(symbol) })),
            targets: targets(), settings: SETTINGS, events: EVENTS,
            feed: { connected: liveOk, error: liveOk ? '' : 'demo', sources: ['binance'], last_tick: now() },
            icons: COINS.map((c) => c[1]), server_time: now(),
        };
    }
    // candles: minutes -> Binance interval with ~60-100 bars, else a calm random walk
    const IV = { 60: ['1m', 60], 360: ['5m', 72], 1440: ['15m', 96], 10080: ['2h', 84], 43200: ['8h', 90] };
    async function candles(symbol, minutes) {
        const [iv, n] = IV[minutes] ?? ['15m', 96];
        try {
            const r = await fetchReal(`${BINANCE}klines?symbol=${symbol}&interval=${iv}&limit=${n}`);
            if (!r.ok)
                throw new Error(String(r.status));
            const k = (await r.json());
            return { symbol, candles: k.map((x) => ({ ts: Math.floor(x[0] / 1000), o: +x[1], h: +x[2], l: +x[3], c: +x[4], v: +x[5] })) };
        }
        catch {
            const step = (minutes * 60) / n;
            let p = live[symbol]?.price ?? 1;
            const out = [];
            for (let i = n; i > 0; i--) {
                const o = p;
                p = p * (1 + (Math.sin(i * 1.7) + Math.cos(i * 0.6)) * 0.002);
                out.push({ ts: Math.floor((now() - i * step) / 60) * 60, o, h: Math.max(o, p), l: Math.min(o, p), c: p, v: 1 });
            }
            return { symbol, candles: out };
        }
    }
    // ---------------------------------------------------------------- the fake API --
    const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    window.fetch = async (input, opts = {}) => {
        const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        if (!/^api\//.test(url))
            return fetchReal(input, opts);
        const method = (opts.method || 'GET').toUpperCase();
        if (method !== 'GET') {
            toast(T.locked);
            return json({ error: T.locked }, 403);
        }
        const u = new URL(url, location.href);
        const p = u.pathname.replace(/^.*\/api\//, '');
        if (p === 'state') {
            await firstPull;
            return json(state());
        }
        if (p === 'candles')
            return json(await candles(u.searchParams.get('symbol') || 'BTCUSDT', Number(u.searchParams.get('minutes')) || 1440));
        if (p === 'news')
            return json({ news: NEWS });
        if (p === 'news/health')
            return json(HEALTH);
        return json({ error: T.locked }, 403);
    };
    // api/stream: Binance every 5 s, delivered the way the tracker's SSE feed does it
    const RealES = window.EventSource;
    // A constructor function, not a class: app.ts only uses onmessage/onerror/close, so a small object stands in.
    window.EventSource = function (url, cfg) {
        if (!/^api\/stream/.test(String(url)))
            return new RealES(url, cfg);
        const es = { onmessage: null, onerror: null, readyState: 1, close() { clearInterval(this._t); this.readyState = 2; } };
        const push = (m) => es.onmessage && es.onmessage({ data: JSON.stringify(m) });
        const tick = async () => {
            await pullPrices();
            push({ type: 'status', connected: liveOk, error: liveOk ? '' : 'demo', sources: ['binance'] });
            const prices = {};
            for (const [s] of COINS) {
                const l = live[s];
                if (l)
                    prices[s] = { price: l.price, change24h: l.change24h };
            }
            push({ type: 'prices', prices });
        };
        es._t = setInterval(tick, 5000);
        setTimeout(tick, 300);
        return es;
    };
    // ------------------------------------------------------------- banner + toast --
    let toastTimer = 0;
    function toast(text) {
        let el = document.getElementById('demoToast');
        if (!el) {
            el = document.createElement('div');
            el.id = 'demoToast';
            document.body.appendChild(el);
        }
        const box = el;
        box.textContent = text;
        box.className = 'show';
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { box.className = ''; }, 2600);
    }
    const css = document.createElement('style');
    css.textContent = `
    .demoBar{position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:center;gap:14px;
      flex-wrap:wrap;padding:10px clamp(16px,4vw,40px);background:rgba(11,13,20,.92);border-bottom:1px solid var(--line);
      font-size:13px;color:var(--fg);text-align:center}
    .demoBar b{font-weight:600}
    .demoBar a{border-radius:9px;padding:8px 16px;font:600 13px Inter,sans-serif;color:#fff;text-decoration:none;
      background:linear-gradient(135deg,var(--accent),var(--accent2));white-space:nowrap}
    .demoBar a:hover{filter:brightness(1.12)}
    .demoBar a:focus-visible{outline:2px solid #fff;outline-offset:2px}
    #demoToast{position:fixed;left:50%;bottom:24px;transform:translate(-50%,20px);opacity:0;pointer-events:none;
      background:#1b2040;border:1px solid var(--line);color:var(--fg);padding:10px 16px;border-radius:10px;font-size:13px;
      transition:opacity .2s,transform .2s;z-index:30}
    #demoToast.show{opacity:1;transform:translate(-50%,0)}
    @media (prefers-reduced-motion:reduce){#demoToast{transition:none}}`;
    document.head.appendChild(css);
    document.addEventListener('DOMContentLoaded', () => {
        const bar = document.createElement('div');
        bar.className = 'demoBar';
        bar.setAttribute('role', 'note');
        bar.innerHTML = `<span><b>${T.head}</b> ${T.note}</span><a href="./?login">${T.login}</a>`;
        document.body.prepend(bar);
    });
})();
