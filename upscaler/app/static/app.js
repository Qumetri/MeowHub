"use strict";
// The owner's page: upload, follow the queue, compare before/after, download.
// Everything is relative (Caddy strips the secret prefix), and every change sends X-Upscale: 1.
// Source of ../app/static/app.js: edit here, then `npm run build` (tsc) — never edit the emitted file.
/** querySelector that insists the element exists: every id used here is in index.html. */
function $(s) {
    const el = document.querySelector(s);
    if (!el)
        throw new Error('missing element ' + s);
    return el;
}
const NAMES = {
    'upscayl-standard-4x': ['Стандарт', 'Сбалансированная детализация, по умолчанию в Upscayl'],
    'ultramix-balanced-4x': ['Больше деталей', 'Естественная текстура: мех, листва, ткань'],
    'high-fidelity-4x': ['Бережно', 'Мягко и точно: портреты, кожа, уже хорошие фото'],
    'remacri-4x': ['Чётко', 'Резкие контрастные детали'],
    'ultrasharp-4x': ['Максимум резкости', 'Самая резкая, может пересушить края'],
    'upscayl-lite-4x': ['Быстро', 'В 3 раза быстрее, деталей чуть меньше'],
    'digital-art-4x': ['Рисунки и аниме', 'Рисунки, аниме, плоские цвета, лайн-арт'],
};
const MAIN = ['upscayl-standard-4x', 'ultramix-balanced-4x', 'high-fidelity-4x', 'digital-art-4x']; // the rest under "Другие модели"
const STATUS = { queued: 'в очереди', running: 'обработка', done: 'готово', error: 'ошибка', cancelled: 'отменено' };
const ERRORS = {
    bad_image: 'Не удалось прочитать файл как картинку.',
    too_large: 'Файл слишком большой.',
    too_many_pixels: 'Слишком много пикселей на входе.',
    already_large: 'Фото уже большое: даже 2× выйдет за предел.',
    upload_cut: 'Загрузка оборвалась.',
    bad_model: 'Модель не найдена.',
};
let info = { max_mb: 25, max_out_mp: 64, keep_hours: 24 };
let jobs = [];
let timer = 0;
const mb = (b) => (b >= 1048576 ? (b / 1048576).toFixed(1) + ' МБ' : Math.max(1, Math.round(b / 1024)) + ' КБ');
function dur(sec) {
    const s = Math.max(1, Math.round(sec));
    if (s < 60)
        return s + ' с';
    const m = Math.floor(s / 60), r = s % 60;
    return m < 60 ? m + ' мин' + (r && m < 5 ? ' ' + r + ' с' : '') : Math.floor(m / 60) + ' ч ' + (m % 60) + ' мин';
}
function plural(n, one, few, many) {
    const a = n % 10, b = n % 100;
    return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many;
}
const errorText = (d, status) => (d.error && ERRORS[d.error]) || d.error || 'Ошибка ' + status;
async function api(path, opts = {}) {
    const r = await fetch('./api/' + path, { ...opts, headers: { 'X-Upscale': '1', ...(opts.headers || {}) } });
    const d = await r.json().catch(() => ({}));
    if (!r.ok)
        throw new Error(errorText(d, r.status));
    return d;
}
// ---- options ----
async function loadModels() {
    const d = await api('models');
    info = d;
    $('#keep').textContent = d.keep_hours + ' ч';
    $('#maxout').textContent = String(d.max_out_mp);
    $('#limits').textContent = `JPG, PNG, WebP · до ${d.max_mb} МБ · до ${d.max_in_mp} Мп`;
    const saved = localGet('model') || 'upscayl-standard-4x';
    const models = $('#models');
    models.innerHTML = '';
    const more = document.createElement('details');
    more.className = 'more';
    more.innerHTML = '<summary>Другие модели</summary><div class="models"></div>';
    const moreList = more.lastElementChild;
    for (const m of d.models) {
        const [name, desc] = NAMES[m.id] || [m.name, m.desc];
        const l = document.createElement('label');
        l.className = 'model';
        l.innerHTML = '<input type="radio" name="model"><b></b><small></small>';
        const input = l.querySelector('input');
        input.value = m.id;
        input.checked = m.id === saved;
        l.querySelector('b').textContent = name;
        l.querySelector('small').textContent = `${desc} · ${m.name}`;
        (MAIN.includes(m.id) ? models : moreList).append(l);
        if (input.checked && !MAIN.includes(m.id))
            more.open = true;
    }
    if (moreList.children.length)
        models.append(more);
    if (!document.querySelector('input[name=model]:checked')) {
        const first = document.querySelector('input[name=model]');
        if (first)
            first.checked = true;
    }
    for (const k of ['scale', 'format']) {
        const v = localGet(k);
        const el = v && document.querySelector(`input[name=${k}][value="${v}"]`);
        if (el)
            el.checked = true;
    }
}
function localGet(k) { try {
    return localStorage.getItem('up_' + k);
}
catch {
    return null;
} }
function localSet(k, v) { try {
    localStorage.setItem('up_' + k, v);
}
catch { /* private mode */ } }
document.addEventListener('change', (e) => {
    const t = e.target;
    if (t instanceof HTMLInputElement && ['model', 'scale', 'format'].includes(t.name))
        localSet(t.name, t.value);
});
const val = (n) => document.querySelector(`input[name=${n}]:checked`)?.value ?? '';
// ---- upload ----
function upload(file) {
    const row = document.createElement('div');
    row.className = 'up';
    row.innerHTML = '<div class="up__n"><span></span><span>0%</span></div><div class="bar"><i style="transform:scaleX(0)"></i></div>';
    row.querySelector('span').textContent = file.name;
    const pct = row.querySelector('.up__n span:last-child');
    $('#uploads').append(row);
    const fail = (msg) => {
        row.classList.add('up--err');
        pct.textContent = '';
        row.querySelector('.bar')?.replaceWith(Object.assign(document.createElement('div'), { textContent: msg }));
        setTimeout(() => row.remove(), 8000);
    };
    if (file.size > info.max_mb * 1048576) {
        fail(`Больше ${info.max_mb} МБ.`);
        return Promise.resolve();
    }
    return new Promise((resolve) => {
        const x = new XMLHttpRequest();
        x.open('POST', `./api/jobs?model=${encodeURIComponent(val('model'))}&scale=${val('scale')}&format=${val('format')}`);
        x.setRequestHeader('X-Upscale', '1');
        x.setRequestHeader('X-Filename', encodeURIComponent(file.name));
        x.setRequestHeader('X-Owner-Name', encodeURIComponent('Владелец (веб)'));
        x.setRequestHeader('Content-Type', 'application/octet-stream');
        x.upload.onprogress = (e) => {
            if (!e.lengthComputable)
                return;
            const p = e.loaded / e.total;
            row.querySelector('.bar i').style.transform = `scaleX(${p})`;
            pct.textContent = Math.round(p * 100) + '%';
        };
        x.onload = () => {
            let d = {};
            try {
                d = JSON.parse(x.responseText);
            }
            catch { /* not JSON */ }
            if (x.status >= 200 && x.status < 300) {
                row.remove();
                void refresh();
            }
            else
                fail((d.error && ERRORS[d.error]) || 'Ошибка ' + x.status);
            resolve();
        };
        x.onerror = () => { fail('Сеть недоступна.'); resolve(); };
        x.send(file);
    });
}
async function uploadAll(files) {
    for (const f of files)
        await upload(f); // one at a time keeps the queue order = pick order
}
const isImage = (f) => f.type.startsWith('image/');
const drop = $('#drop');
const picker = $('#file');
picker.addEventListener('change', () => { void uploadAll([...(picker.files ?? [])]); picker.value = ''; });
drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    picker.click();
} });
for (const ev of ['dragenter', 'dragover'])
    drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); });
for (const ev of ['dragleave', 'drop'])
    drop.addEventListener(ev, () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => { e.preventDefault(); void uploadAll([...(e.dataTransfer?.files ?? [])].filter(isImage)); });
document.addEventListener('paste', (e) => {
    const files = [...(e.clipboardData?.files ?? [])].filter(isImage);
    if (files.length)
        void uploadAll(files);
});
// ---- list ----
function render(data) {
    jobs = data.jobs;
    const q = data.queue;
    const waiting = q.queue.length + (q.current ? 1 : 0);
    $('#queue').innerHTML = waiting
        ? `<span class="dot dot--busy"></span> В работе <b>${waiting}</b> ${plural(waiting, 'задание', 'задания', 'заданий')}, всё будет готово через <b>${dur((q.current?.eta || 0) + q.queue.reduce((a, i) => a + i.est, 0))}</b>`
        : '<span class="dot"></span> Видеокарта свободна';
    $('#count').textContent = jobs.length ? String(jobs.length) : '';
    const list = $('#list');
    if (!jobs.length) {
        list.innerHTML = '<div class="empty">Пока пусто. Добавь фото слева: можно сразу несколько, перетащить или вставить из буфера (Ctrl+V).</div>';
        return;
    }
    const pos = {};
    let ahead = q.current ? q.current.eta : 0;
    q.queue.forEach((i) => { ahead += i.est; pos[i.id] = ahead; });
    list.querySelector('.empty')?.remove();
    const seen = new Set();
    const tpl = $('#t-job');
    jobs.forEach((j, idx) => {
        seen.add(j.id);
        let el = list.querySelector(`[data-id="${j.id}"]`);
        if (!el) {
            el = tpl.content.firstElementChild.cloneNode(true);
            el.dataset.id = j.id;
            el.querySelector('.job__thumb').addEventListener('click', () => openViewer(j.id));
        }
        if (list.children[idx] !== el)
            list.insertBefore(el, list.children[idx] || null);
        fill(el, j, j.id === q.current?.id ? q.current.eta : pos[j.id]);
    });
    for (const el of [...list.children])
        if (!seen.has(el.dataset.id ?? ''))
            el.remove();
}
function fill(el, j, eta) {
    const q = (s) => el.querySelector(s);
    const img = q('.job__thumb img');
    const src = `./api/jobs/${j.id}/${j.status === 'done' ? 'after' : 'before'}`;
    if (img.getAttribute('src') !== src)
        img.src = src;
    q('.job__thumb').disabled = j.status !== 'done';
    q('.job__name').textContent = j.name;
    const pill = q('.pill');
    pill.className = 'pill pill--' + j.status;
    pill.textContent = STATUS[j.status] || j.status;
    const model = (NAMES[j.model] || [j.model])[0];
    const who = j.owner_name || (j.owner_id === 'web' ? '' : 'id ' + j.owner_id);
    const parts = [`${j.in_w}×${j.in_h} → ${j.out_w}×${j.out_h}`, model];
    if (j.scale !== j.scale_asked)
        parts.push(`${j.scale}× вместо ${j.scale_asked}×`);
    if (j.status === 'done')
        parts.push(mb(j.out_size), dur(j.took));
    if (j.status === 'queued' && eta)
        parts.push('готово через ' + dur(eta));
    if (who)
        parts.push(who);
    q('.job__meta').textContent = parts.join(' · ');
    const bar = q('.bar');
    bar.hidden = j.status !== 'running';
    bar.querySelector('i').style.transform = `scaleX(${Math.max(0.03, j.progress / 100)})`;
    const err = q('.job__err');
    err.hidden = j.status !== 'error';
    err.textContent = j.error;
    const act = q('.job__act');
    const key = j.status;
    if (act.dataset.key === key)
        return;
    act.dataset.key = key;
    act.innerHTML = '';
    if (j.status === 'done') {
        act.append(button('Сравнить', () => openViewer(j.id)));
        const a = Object.assign(document.createElement('a'), { className: 'btn btn--primary', textContent: 'Скачать', href: `./api/jobs/${j.id}/result?download=1` });
        act.append(a);
    }
    if (j.status === 'queued' || j.status === 'running')
        act.append(button('Отменить', () => api(`jobs/${j.id}/cancel`, { method: 'POST' }).then(refresh)));
    else
        act.append(button('Удалить', () => api(`jobs/${j.id}`, { method: 'DELETE' }).then(refresh), 'btn--danger'));
}
function button(text, fn, cls = '') {
    const b = Object.assign(document.createElement('button'), { type: 'button', className: 'btn ' + cls, textContent: text });
    b.addEventListener('click', () => {
        b.disabled = true;
        Promise.resolve(fn()).catch((e) => alert(e instanceof Error ? e.message : String(e))).finally(() => { b.disabled = false; });
    });
    return b;
}
async function refresh() {
    clearTimeout(timer);
    let busy = false;
    try {
        const d = await api('jobs');
        render(d);
        busy = d.jobs.some((j) => j.status === 'queued' || j.status === 'running');
    }
    catch (e) {
        $('#queue').textContent = 'Сервис недоступен: ' + (e instanceof Error ? e.message : String(e));
    }
    if (!document.hidden)
        timer = setTimeout(refresh, busy ? 1500 : 15000);
}
document.addEventListener('visibilitychange', () => { if (!document.hidden)
    void refresh(); });
// ---- before / after ----
const viewer = $('#viewer');
const range = $('#v-range');
function setAt(p) {
    $('#cmp').style.setProperty('--at', p + '%');
}
function openViewer(id) {
    const j = jobs.find((x) => x.id === id);
    if (!j || j.status !== 'done')
        return;
    $('#v-title').textContent = j.name;
    $('#v-meta').textContent = `${j.in_w}×${j.in_h} → ${j.out_w}×${j.out_h} · ${mb(j.out_size)}`;
    $('#v-before').src = `./api/jobs/${id}/before`;
    $('#v-after').src = `./api/jobs/${id}/after`;
    $('#v-dl').href = `./api/jobs/${id}/result?download=1`;
    $('#v-open').href = `./api/jobs/${id}/result`;
    range.value = '50';
    setAt(50);
    viewer.showModal();
}
range.addEventListener('input', () => setAt(range.value));
$('#v-close').addEventListener('click', () => viewer.close());
viewer.addEventListener('click', (e) => { if (e.target === viewer)
    viewer.close(); });
loadModels().catch((e) => { $('#models').textContent = 'Не удалось загрузить модели: ' + (e instanceof Error ? e.message : String(e)); });
void refresh();
