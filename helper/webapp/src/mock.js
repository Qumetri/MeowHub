// Mock data. api.js reaches it behind `import.meta.env.DEV` (?mock=...), and
// App.jsx loads it lazily via import() for the owner's "preview as member" -
// either way it is its own chunk, never part of the main bundle. Dev switches: ?mock=member|owner|stranger|expired
// (+ &mode=tg for the fake Telegram shell, &theme=dark, &lang=en, &pending=1, &fail=<path part>,
// &member_bot=1 (member bot configured), &via=member, &preview=guest|member|expired, &tab=overview).
import { ApiError } from './api.js'
import { MOCK_USERS } from './mock-users.js'
import { getLang } from './i18n.js'

const P = new URLSearchParams(location.search)
let ROLE = P.get('mock')
let previewOn = false
const D = 86400
const now = () => Math.floor(Date.now() / 1000)
const L = () => (previewOn ? getLang() : P.get('lang') === 'en' ? 'en' : 'ru')
const SERVER = 'example.org'
const delay = (ms) => new Promise((r) => setTimeout(r, ms))

const U = (id, first_name, last_name, username, days, services, seenAgo, createdDays, extra = {}) => ({
  id, first_name, last_name, username, lang: 'ru', services, suspended: 0,
  expires_ts: days == null ? null : now() + Math.round(days * D),
  created_ts: now() - createdDays * D, last_seen_ts: seenAgo == null ? 0 : now() - seenAgo,
  note: '', ...extra,
})
const state = {
  members: [
    U(100200300, 'Админ', '', 'owner', null, ['vpn', 'matrix', 'tools'], 60, 220, { note: 'Владелец' }),
    U(100200301, 'Алексей', 'Громов', 'agromov', 29 - 0.4, ['vpn', 'matrix'], 12 * 60, 41),
    U(100200302, 'Мария', 'Соколова', 'msokolova', 1.6, ['vpn', 'matrix', 'tools'], 3 * 3600, 90, { note: 'Сестра Алексея' }),
    U(100200303, 'Дмитрий', 'Орлов', 'dorlov', -3, ['vpn', 'matrix'], 5 * D, 70),
    U(100200304, 'Анастасия', 'Белова', '', 63.5, ['vpn'], 40, 12),
    U(100200305, 'Иван', 'Петров', 'ivanp', 19.7, ['vpn', 'matrix'], 2 * D, 33, { suspended: 1, note: 'Просил паузу до осени' }),
    U(100200306, 'Екатерина', 'Волкова', 'kvolkova', 5.2, ['vpn', 'matrix'], 26 * 3600, 25),
    U(100200307, 'Сергей', 'Новиков', 'snovikov', 119.5, ['vpn', 'matrix', 'tools'], 90, 150),
    U(100200308, 'Ольга', 'Морозова', 'omorozova', -40, ['vpn'], 38 * D, 200),
    U(100200309, 'Никита', 'Фёдоров', 'nfedorov', 13.3, ['vpn', 'matrix'], 4 * 3600, 18),
    U(100200310, 'Татьяна', 'Зайцева', 'tzaitseva', 0.6, ['vpn', 'matrix'], 9 * 60, 29),
    U(100200311, 'Артём', 'Кузнецов', 'akuznetsov', 44.1, ['vpn'], 6 * D, 8),
    U(100200312, 'Полина', 'Смирнова', 'psmirnova', 87.9, ['vpn', 'matrix'], 15 * 60, 5),
    U(100200313, 'Максим', 'Лебедев', 'mlebedev', -2, ['vpn'], 12 * D, 120, { suspended: 1 }),
  ],
  matrix: {
    100200301: [{ mxid: '@agromov:' + SERVER, created_ts: now() - 30 * D, locked: false }],
    100200303: [{ mxid: '@dmitry:' + SERVER, created_ts: now() - 60 * D, locked: true }],
    100200302: [{ mxid: '@masha:' + SERVER, created_ts: now() - 50 * D, locked: false }, { mxid: '@m.sokolova:' + SERVER, created_ts: now() - 20 * D, locked: false }],
    100200300: [{ mxid: '@admin:' + SERVER, created_ts: now() - 100 * D, locked: false }],
  },
  codes: [
    { code: 'MEOW-K7QM-3XDP', days: 30, services: ['vpn', 'matrix'], uses_max: 1, uses: 0, created_ts: now() - 2 * D, valid_until: now() + 28 * D, revoked: 0, note: 'Для Ани' },
    { code: 'MEOW-H2WR-9TNB', days: 90, services: ['vpn', 'matrix', 'tools'], uses_max: 1, uses: 0, created_ts: now() - 5 * D, valid_until: now() + 25 * D, revoked: 0, note: '' },
    { code: 'MEOW-B5CE-4YVA', days: 30, services: ['vpn'], uses_max: 5, uses: 2, created_ts: now() - 9 * D, valid_until: now() + 21 * D, revoked: 0, note: 'Друзья' },
    { code: 'MEOW-X8LD-2FGJ', days: 30, services: ['vpn', 'matrix'], uses_max: 1, uses: 1, created_ts: now() - 20 * D, valid_until: now() + 10 * D, revoked: 0, note: '' },
    { code: 'MEOW-P3ZK-7RSH', days: 30, services: ['vpn', 'matrix'], uses_max: 1, uses: 0, created_ts: now() - 45 * D, valid_until: now() - 15 * D, revoked: 0, note: '' },
    { code: 'MEOW-N6UV-5AMC', days: 180, services: ['vpn', 'matrix'], uses_max: 1, uses: 0, created_ts: now() - 12 * D, valid_until: now() + 18 * D, revoked: 1, note: 'Ошибся' },
  ],
  inbounds: [
    { id: 1, remark: 'Speed · Vision Reality', protocol: 'vless', port: 443, enable: true, member: true },
    { id: 2, remark: 'Resistance · XHTTP Reality', protocol: 'vless', port: 59200, enable: true, member: true },
    { id: 3, remark: 'Trojan TLS', protocol: 'trojan', port: 8443, enable: true, member: true },
    { id: 4, remark: 'Shadowsocks 2022', protocol: 'shadowsocks', port: 8388, enable: true, member: true },
    { id: 5, remark: 'VMess WebSocket', protocol: 'vmess', port: 2053, enable: true, member: false },
    { id: 6, remark: 'Hysteria2', protocol: 'hysteria', port: 4443, enable: true, member: false },
    { id: 7, remark: 'AmneziaWG', protocol: 'amneziawg', port: 20443, enable: true, member: true },
    { id: 8, remark: 'WireGuard', protocol: 'wireguard', port: 51820, enable: true, member: false },
    { id: 9, remark: '🧪 Test inbound', protocol: 'vless', port: 10001, enable: false, member: false },
    { id: 10, remark: 'MTProto proxy', protocol: 'mtproto', port: 9443, enable: true, member: false },
  ],
  sync: { ts: now() - 340, ok: true, error: '', vpn_clients: 11 },
  keys: {
    helper: { source: 'env', masked: '8123…a9Zk', bot: { id: 8123, username: 'ExampleBot', name: 'MeowHub Helper', ok: true, error: '' } },
    member: P.get('member_bot') === '1'
      ? { source: 'page', masked: '7345…Qm2x', bot: { id: 7345, username: 'ExampleMemberBot', name: 'MeowHub', ok: true, error: '' } }
      : { source: 'none', masked: '', bot: null },
    crypto: { source: 'env', masked: '6011…Lp0d', bot: { id: 6011, username: 'ExampleCryptoBot', name: 'Crypto News', ok: false, error: 'api.telegram.org: timeout' } },
    xui: { source: 'page', masked: 'x7Kq…93Fa', check: { ok: true, error: '', detail: '25 инбаундов' } },
  },
}
const NOTE_CRYPTO = 'n8n хранит свою копию токена крипто-бота: после смены обнови токен в его Telegram-credential в n8n.'
const keyItem = (id) => {
  const k = state.keys[id]
  return {
    id, kind: id === 'xui' ? 'api' : 'bot', configured: k.source !== 'none', source: k.source, masked: k.masked,
    updated_ts: k.source === 'page' ? now() - 3600 : null, restart_on_change: id === 'helper' || id === 'member',
    ...(k.bot !== undefined ? { bot: k.bot } : {}), ...(k.check ? { check: k.check } : {}),
    ...(id === 'crypto' ? { note: NOTE_CRYPTO } : {}),
  }
}
const pickSelf = (r) => (r === 'stranger' ? null : (r === 'owner' ? MOCK_USERS.owner : r === 'expired' ? MOCK_USERS.expired : MOCK_USERS.member))
let SELF = pickSelf(ROLE)
let TG_USER = MOCK_USERS[ROLE] || MOCK_USERS.member
// Owner preview: act as 'stranger' | 'member' | 'expired'; configure(null) goes back to the URL's role.
export function configure(role) {
  previewOn = !!role
  ROLE = role || P.get('mock')
  SELF = pickSelf(ROLE)
  TG_USER = MOCK_USERS[ROLE] || MOCK_USERS.member
}
let pendingOnce = P.get('pending') === '1'

const find = (id) => state.members.find((m) => m.id === Number(id))
const statusOf = (m) => (m.suspended ? 'suspended' : m.expires_ts != null && m.expires_ts <= now() ? 'expired' : 'active')
const rnd = (id, k) => { const x = Math.sin(id * 12.9898 + k * 78.233) * 43758.5453; return x - Math.floor(x) }

function view(m) {
  const st = statusOf(m)
  return {
    id: m.id, username: m.username, first_name: m.first_name, last_name: m.last_name, lang: m.lang,
    status: st, expires_ts: m.expires_ts,
    days_left: m.expires_ts == null ? null : st === 'expired' ? 0 : Math.ceil((m.expires_ts - now()) / D),
    services: m.services, created_ts: m.created_ts, last_seen_ts: m.last_seen_ts, note: m.note,
    vpn_email: 'mh-' + m.id, has_vpn_client: m.services.includes('vpn'),
    matrix: (state.matrix[m.id] || []).map((a) => a.mxid),
  }
}
const traffic = (m) => (m.services.includes('vpn')
  ? { up: Math.round(rnd(m.id, 1) * 3.2e9), down: Math.round(rnd(m.id, 2) * 4.5e10) } : null)
const online = (m) => m.services.includes('vpn') && rnd(m.id, 3) > 0.6 && statusOf(m) === 'active'

function codeView(c) {
  const state_ = c.revoked ? 'revoked' : c.uses >= c.uses_max ? 'used' : c.valid_until <= now() ? 'expired' : 'live'
  return { ...c, state: state_, share_url: 'https://t.me/ExampleBot?start=' + c.code }
}

function activity() {
  const out = []
  for (let i = 29; i >= 0; i--) {
    const d = new Date(Date.now() - i * D * 1000)
    const day = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    const wk = [0, 6].includes(d.getDay()) ? 0.7 : 1
    const base = 3 + 7 * rnd(i, 7) + (29 - i) * 0.12
    const users = i === 11 || i === 12 ? 0 : Math.round(base * wk)
    out.push({ day, users, messages: users ? Math.round(users * (6 + 20 * rnd(i, 8))) : 0, app: users ? Math.round(users * (1 + 3 * rnd(i, 9))) : 0 })
  }
  return out
}

function botAvatar(h) {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><rect width="80" height="80" fill="hsl(${h} 60% 45%)"/><rect x="20" y="26" width="40" height="32" rx="10" fill="#fff" fill-opacity=".9"/><circle cx="32" cy="42" r="4" fill="hsl(${h} 60% 45%)"/><circle cx="48" cy="42" r="4" fill="hsl(${h} 60% 45%)"/><rect x="38" y="16" width="4" height="10" fill="#fff" fill-opacity=".9"/></svg>`
  return 'data:image/svg+xml,' + encodeURIComponent(svg)
}

const SERVICES = [
  { id: 'vpn', ru: ['VPN', 'Защищённый доступ в интернет'], en: ['VPN', 'Secure internet access'] },
  { id: 'matrix', ru: ['Мессенджер', 'Приватный чат Matrix'], en: ['Messenger', 'Private Matrix chat'] },
  { id: 'tools', ru: ['Инструменты бота', 'Проверки, ссылки, скачивание видео'], en: ['Bot tools', 'Checks, links, video downloads'] },
]
const err = (status, error, message) => { throw new ApiError(status, error, message) }
const selfMember = () => (SELF ? find(SELF.id) : null)
const hasSvc = (s) => { const m = selfMember(); return m && statusOf(m) === 'active' && m.services.includes(s) }
const ownerOnly = () => { if (ROLE !== 'owner') err(403, 'forbidden', 'Только для владельца') }

export async function handle(method, path, body) {
  await delay(180 + Math.random() * 160)
  const failPart = P.get('fail')
  if (failPart && path.includes(failPart)) err(500, 'boom', 'Внутренняя ошибка сервера (mock)')
  const seg = path.split('?')[0].split('/')

  if (path === 'me') {
    const m = selfMember()
    // ?via=member: the owner opened the app through the member bot - plain member/stranger.
    const viaMember = !previewOn && ROLE === 'owner' && P.get('via') === 'member'
    return {
      role: ROLE === 'owner' && !viaMember ? 'owner' : m ? 'member' : 'stranger',
      mode: P.get('mode') === 'tg' ? 'tg' : 'browser',
      user: { ...TG_USER, language_code: L() },
      member: m ? view(m) : null, contact: '@owner', bot_username: 'ExampleBot',
      via: viaMember ? 'member' : P.get('mode') === 'tg' ? 'helper' : 'browser',
      member_bot_username: state.keys.member.bot?.username || '',
      can_preview: ROLE === 'owner',
      services: SERVICES.map((s) => ({ id: s.id, name: s[L()][0], description: s[L()][1] })),
    }
  }

  if (path === 'redeem') {
    const code = (body.code || '').toUpperCase()
    const tail = code.slice(-4)
    const bad = { USED: 'used', EXPR: 'expired_code', REVK: 'revoked_code', RATE: 'rate_limited', SUSP: 'suspended' }[tail]
    if (bad) return { result: bad, member: null }
    if (!code.startsWith('MEOW')) return { result: 'invalid', member: null }
    let m = selfMember()
    if (!m) {
      m = U(TG_USER.id, TG_USER.first_name, TG_USER.last_name, TG_USER.username, 30, ['vpn', 'matrix'], 0, 0)
      state.members.unshift(m)
      return { result: 'new', member: view(m) }
    }
    m.expires_ts = Math.max(now(), m.expires_ts || now()) + 30 * D
    m.suspended = 0
    return { result: 'extended', member: view(m) }
  }

  if (path === 'vpn') {
    if (!hasSvc('vpn')) err(403, 'no_access', 'Нет доступа')
    if (pendingOnce) { pendingOnce = false; err(409, 'pending', 'Клиент ещё создаётся') }
    const sub = 'https://' + SERVER + '/sub/k3x9a7fq2mzp81vd'
    const go = (a) => `./go/${a}?u=${encodeURIComponent(sub)}`
    return {
      sub_url: sub,
      links: [
        { name: 'Speed · Vision Reality', url: 'vless://6f2a1c3e-91b4-4d0e-8a57-0c9d1b2e3f40@' + SERVER + ':443?security=reality&flow=xtls-rprx-vision&sni=www.icloud.com#Speed' },
        { name: 'Resistance · XHTTP', url: 'vless://6f2a1c3e-91b4-4d0e-8a57-0c9d1b2e3f40@' + SERVER + ':443?type=xhttp&security=reality&sni=dl.google.com#Resistance' },
        { name: 'Trojan TLS', url: 'trojan://q7Zp2mXk9d@' + SERVER + ':8443?sni=' + SERVER + '#Trojan' },
        { name: 'Shadowsocks 2022', url: 'ss://MjAyMi1ibGFrZTMtYWVzLTI1Ni1nY206a2V5@' + SERVER + ':8388#SS' },
      ],
      groups: [
        { id: 'main', title: 'VLESS и Shadowsocks', apps: ['Happ', 'v2RayTun', 'v2rayNG', 'Hiddify'],
          hint: 'Скопируй → в приложении «+» → «Импорт из буфера».',
          links: [
            { name: 'Speed · Vision Reality', url: 'vless://6f2a1c3e-91b4-4d0e-8a57-0c9d1b2e3f40@' + SERVER + ':443?security=reality&flow=xtls-rprx-vision&sni=www.icloud.com#Speed', action: 'copy' },
            { name: 'Resistance · XHTTP', url: 'vless://6f2a1c3e-91b4-4d0e-8a57-0c9d1b2e3f40@' + SERVER + ':443?type=xhttp&security=reality&sni=dl.google.com#Resistance', action: 'copy' },
            { name: 'Trojan TLS', url: 'trojan://q7Zp2mXk9d@' + SERVER + ':8443?sni=' + SERVER + '#Trojan', action: 'copy' },
            { name: 'Shadowsocks 2022', url: 'ss://MjAyMi1ibGFrZTMtYWVzLTI1Ni1nY206a2V5@' + SERVER + ':8388#SS', action: 'copy' },
          ] },
        { id: 'new', title: '🧪 Новые протоколы (тест)', apps: ['Happ', 'v2RayTun'],
          hint: 'Нужна последняя версия Happ или v2RayTun. Скопируй → «+» → «Из буфера».',
          links: [{ name: 'VLESS Encryption (ML-KEM)', url: 'vless://6f2a1c3e-91b4-4d0e-8a57-0c9d1b2e3f40@' + SERVER + ':2096?encryption=mlkem768x25519plus.native.0rtt.xxxx&type=tcp#🧪 Enc', action: 'copy' }] },
        { id: 'udp', title: 'Hysteria2 (UDP)', apps: ['Happ', 'Hiddify', 'v2RayTun'],
          hint: 'Если обычные не работают. Скопируй → «+» → «Из буфера».',
          links: [{ name: 'Hysteria2', url: 'hysteria2://k3x9a7fq2m@' + SERVER + ':4443?sni=' + SERVER + '#Hysteria2', action: 'copy' }] },
        { id: 'tg', title: 'Прокси для Telegram', apps: ['Telegram'],
          hint: 'Нажми — Telegram сам предложит включить.',
          links: [{ name: 'MTProto-прокси', url: 'https://t.me/proxy?server=' + SERVER + '&port=9443&secret=ee1f2e3d4c5b6a79880a1b2c3d4e5f6a7b', action: 'telegram' }] },
      ],
      traffic: { up: 1.2e9, down: 18.7e9 }, online: true,
      apps: [
        { id: 'happ', name: 'Happ', platforms: 'iOS · Android · macOS', go_url: go('happ') },
        { id: 'hiddify', name: 'Hiddify', platforms: 'iOS · Android · Windows', go_url: go('hiddify') },
        { id: 'v2raytun', name: 'v2RayTun', platforms: 'iOS · Android', go_url: go('v2raytun') },
        { id: 'streisand', name: 'Streisand', platforms: 'iOS', go_url: go('streisand') },
        { id: 'v2rayng', name: 'v2rayNG', platforms: 'Android', go_url: go('v2rayng') },
      ],
    }
  }

  if (path === 'matrix') {
    if (!hasSvc('matrix')) err(403, 'no_access', 'Нет доступа')
    const accounts = state.matrix[SELF.id] || []
    return { server_name: SERVER, client_url: 'https://matrix.' + SERVER, element_url: 'https://app.element.io/#/welcome', accounts, max: 2, can_create: accounts.length < 2 }
  }
  if (path === 'matrix/create') {
    if (!hasSvc('matrix')) err(403, 'no_access', 'Нет доступа')
    if (!/^[a-z0-9._=-]{3,24}$/.test(body.username)) err(400, 'bad_username', 'Недопустимое имя')
    if (body.password.length < 10) err(400, 'weak_password', 'Слишком короткий пароль')
    if (['admin', 'taken'].includes(body.username)) err(409, 'taken', 'Имя занято')
    const list = (state.matrix[SELF.id] ||= [])
    if (list.length >= 2) err(400, 'limit', 'Лимит аккаунтов')
    const mxid = `@${body.username}:${SERVER}`
    list.push({ mxid, created_ts: now(), locked: false })
    return { mxid }
  }
  if (path === 'matrix/password') {
    if (body.password.length < 10) err(400, 'weak_password', 'Слишком короткий пароль')
    return { ok: true }
  }

  // ---- admin
  if (seg[0] === 'admin') {
    ownerOnly()
    const rest = seg.slice(1)
    if (rest[0] === 'overview') {
      const ms = state.members
      const st = ms.map(statusOf)
      return {
        counts: {
          members: ms.length, active: st.filter((s) => s === 'active').length,
          expired: st.filter((s) => s === 'expired').length, suspended: st.filter((s) => s === 'suspended').length,
          expiring_7d: ms.filter((m) => statusOf(m) === 'active' && m.expires_ts != null && m.expires_ts - now() <= 7 * D).length,
        },
        activity: activity(),
        bots: [
          { id: 1, name: 'MeowHub Helper', username: 'ExampleBot', ok: true, error: '', avatar_url: botAvatar(262) },
          { id: 2, name: 'Crypto News', username: 'ExampleCryptoBot', ok: false, error: 'api.telegram.org: timeout', avatar_url: botAvatar(28) },
        ],
        sync: state.sync, vpn_configured: true, matrix_configured: true,
      }
    }
    if (rest[0] === 'sync') { state.sync = { ts: now(), ok: true, error: '', vpn_clients: state.members.filter((m) => m.services.includes('vpn')).length }; return state.sync }
    if (rest[0] === 'integrations') {
      await delay(300)
      if (rest.length === 1) return { items: ['helper', 'member', 'crypto', 'xui'].map(keyItem) }
      const id = rest[1], k = state.keys[id]
      if (!k) err(404, 'not_found', 'Неизвестный ключ')
      if (rest[2] === 'reset') {
        if (k.source !== 'page') err(404, 'no_override', 'Нет ключа, заданного здесь')
        k.source = id === 'member' ? 'none' : 'env'; k.masked = id === 'member' ? '' : k.masked
        if (id === 'member') k.bot = null
        return { item: keyItem(id), restarting: id === 'helper' || id === 'member' }
      }
      const tok = String(body.token || '').trim()
      if (tok.length < 8) err(400, 'bad_token', 'Telegram ответил: Unauthorized — токен не подошёл.')
      if (tok.includes('bad')) err(400, 'bad_token', 'Telegram ответил: Unauthorized — токен не подошёл.')
      if (id === 'member' && tok.includes('same')) err(400, 'same_bot', 'Это тот же бот, что и служебный. Нужен отдельный бот от @BotFather.')
      if (id === 'crypto' && tok.includes('apply')) err(502, 'crypto_apply_failed', 'Крипто-сервис не принял токен (502). Ничего не сохранено.')
      k.source = 'page'; k.masked = tok.slice(0, 4) + '…' + tok.slice(-4)
      if (id === 'xui') k.check = { ok: true, error: '', detail: '25 инбаундов' }
      else k.bot = { id: 9000 + id.length, username: id === 'member' ? 'ExampleMemberBot' : 'NewBot', name: id === 'member' ? 'MeowHub' : 'New Bot', ok: true, error: '' }
      return { item: keyItem(id), restarting: id === 'helper' || id === 'member' }
    }
    if (rest[0] === 'inbounds') {
      if (method === 'POST') {
        const ids = body.member_ids
        const awg = state.inbounds.filter((i) => ids.includes(i.id) && ['wireguard', 'amneziawg'].includes(i.protocol))
        if (awg.length > 1) err(400, 'two_awg', 'Нельзя выдавать два AmneziaWG/WireGuard инбаунда одновременно')
        state.inbounds.forEach((i) => { i.member = ids.includes(i.id) })
      }
      return state.inbounds.map((i) => ({ ...i }))
    }
    if (rest[0] === 'members' && rest.length === 1) {
      const rows = state.members.map((m) => ({
        ...view(m), traffic: traffic(m), online: online(m),
        messages_30d: Math.round(rnd(m.id, 4) * 400), app_30d: Math.round(rnd(m.id, 5) * 40),
      }))
      if (P.get('warn')) Object.defineProperty(rows, 'warning', { value: P.get('warn') })
      return rows
    }
    if (rest[0] === 'members') {
      const m = find(rest[1])
      if (!m) err(404, 'not_found', 'Участник не найден')
      if (method === 'POST') {
        const a = body.action
        if (a === 'extend') m.expires_ts = Math.max(now(), m.expires_ts || now()) + body.days * D
        else if (a === 'set_expiry') m.expires_ts = body.ts
        else if (a === 'suspend') m.suspended = 1
        else if (a === 'resume') m.suspended = 0
        else if (a === 'services') m.services = body.services
        else if (a === 'note') m.note = body.note
        else if (a === 'delete') { state.members = state.members.filter((x) => x !== m); return { deleted: true } }
        return view(m)
      }
      const ev = (k, d, ago, detail = '') => ({ id: k, ts: now() - ago * D, uid: m.id, kind: d, detail })
      return {
        ...view(m), traffic: traffic(m), online: online(m), vpn_links_count: m.services.includes('vpn') ? 4 : 0,
        matrix_accounts: state.matrix[m.id] || [],
        events: [
          ev(5, 'extend', 1.2, '+30 дн.'), ev(4, 'redeem', 31, 'MEOW-X8LD-2FGJ · 30 дн.'),
          ev(3, 'matrix_create', 30, '@' + (m.username || 'user') + ':' + SERVER), ev(2, 'services', 40, 'vpn, matrix'),
          ev(1, 'join', (Date.now() / 1000 - m.created_ts) / D, 'Первый вход'),
        ],
      }
    }
    if (rest[0] === 'grant') {
      let m = find(body.uid)
      if (!m) { m = U(body.uid, 'Новый', 'участник', '', body.days, body.services, null, 0); state.members.unshift(m) } else {
        m.expires_ts = Math.max(now(), m.expires_ts || now()) + body.days * D
        m.services = [...new Set([...m.services, ...body.services])]
      }
      return view(m)
    }
    if (rest[0] === 'codes') {
      if (rest[2] === 'revoke') { const c = state.codes.find((x) => x.code === rest[1]); if (c) c.revoked = 1; return { ok: true } }
      if (method === 'POST') {
        const code = 'MEOW-' + Array.from({ length: 2 }, () => Array.from({ length: 4 }, () => 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'[Math.floor(Math.random() * 32)]).join('')).join('-')
        state.codes.unshift({ code, days: body.days, services: body.services, uses_max: body.uses_max, uses: 0, created_ts: now(), valid_until: now() + 30 * D, revoked: 0, note: body.note || '' })
        return { code, share_url: 'https://t.me/ExampleBot?start=' + code }
      }
      return state.codes.map(codeView)
    }
  }
  return err(404, 'not_found', 'Не найдено: ' + path)
}
