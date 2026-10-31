// Mock data. api.js reaches it behind `import.meta.env.DEV` (?mock=...), and
// App.jsx loads it lazily via import() for the owner's "preview as member" -
// either way it is its own chunk, never part of the main bundle. Dev switches: ?mock=member|owner|stranger|expired
// (+ &mode=tg for the fake Telegram shell, &theme=dark, &lang=en, &pending=1, &fail=<path part>,
// &lang=auto|ru|en (initial stored preference; `auto` follows &lc=ru|en, the simulated Telegram language_code),
// &member_bot=1 (member bot configured), &via=member, &preview=guest|member|expired, &tab=overview).
import { ApiError } from './api'
import { MOCK_USERS, type MockRole } from './mock-users'
import { getLang } from './i18n'
import type {
  AccessCode, AdminService, BookVoice, BooksView, DlJob, DlPresets, Inbound, KeyId, KeyItem, Lang, LangPref,
  MatrixAccount, MemberStatus, MemberBook, PayOffer, PaymentsAdmin, PayProvider, PlanId, SyncState, TgUser,
  UpscaleJob, UpscaleView,
} from './types'

// Stored rows (what the server keeps), as opposed to the views handle() returns.
interface MockMember {
  id: number
  first_name: string
  last_name: string
  username: string
  lang: string
  services: string[]
  suspended: number
  expires_ts: number | null
  created_ts: number
  last_seen_ts: number
  note: string
}
type MockCode = Omit<AccessCode, 'state' | 'share_url'>
interface MockKey { source: KeyItem['source']; masked: string; bot?: KeyItem['bot']; check?: { ok: boolean; error: string } }
/** uid = owner, sim = start time of a simulated job, err_t = [ru, en] error text */
type MockDlJob = DlJob & { uid: number; sim?: number; err_t?: [ru: string, en: string] }
/** Request bodies of every POST the screens send (all optional: each route reads its own). */
interface MockBody {
  lang?: LangPref; code?: string; enabled?: boolean; voice?: string
  url?: string; preset?: string; playlist?: boolean; clip?: unknown; subs?: string
  username?: string; password?: string; member_ids?: number[]
  action?: string; days?: number; ts?: number | null; services?: string[]; note?: string
  uid?: number; token?: string; uses_max?: number
  provider?: string; plan?: string; prices?: Partial<Record<PlanId, number>>; amount?: number; amounts?: number[]
}

// Paid plans (payments.py): both providers on in the mock so the extend screen shows them.
const PLAN_DAYS: Record<PlanId, number> = { m1: 30, m3: 90, m12: 365 }
const PAY: PaymentsAdmin['settings'] = {
  stars: { enabled: true, prices: { m1: 285, m3: 855, m12: 3420 } },
  crypto: { enabled: true, prices: { m1: 3.55, m3: 10.6, m12: 42.3 } },
  donate: { enabled: true, amounts: [50, 100, 250, 500] },
}
const payOffer = (): PayOffer | null => (!PAY.stars.enabled && !PAY.crypto.enabled ? null : {
  stars: PAY.stars.enabled, crypto: PAY.crypto.enabled,
  donate: PAY.donate.enabled && PAY.stars.enabled ? PAY.donate.amounts : null,
  plans: (Object.keys(PLAN_DAYS) as PlanId[]).map((id) => ({ id, days: PLAN_DAYS[id], stars: PAY.stars.prices[id], usd: PAY.crypto.prices[id] })),
})

const P = new URLSearchParams(location.search)
let ROLE: string | null = P.get('mock')
let previewOn = false
const D = 86400
const now = () => Math.floor(Date.now() / 1000)
// Language: `langPref` is the stored preference (POST lang); L() the effective one for /api/me.
// tr() localizes the mock's own messages by the UI language (like the server honouring X-Lang).
let langPref: LangPref = P.get('lang') === 'ru' || P.get('lang') === 'en' ? (P.get('lang') as Lang) : 'auto'
let autoLang: Lang = P.get('lc') === 'en' ? 'en' : 'ru'
const L = (): Lang => (langPref === 'auto' ? autoLang : langPref)
const tr = (ru: string, en: string): string => (getLang() === 'en' ? en : ru)
let realPref: { langPref: LangPref; autoLang: Lang } | null = null
const SERVER = 'example.org'
const delay = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

const U = (id: number, first_name: string, last_name: string, username: string, days: number | null, services: string[],
  seenAgo: number | null, createdDays: number, extra: Partial<MockMember> = {}): MockMember => ({
  id, first_name, last_name, username, lang: 'ru', services, suspended: 0,
  expires_ts: days == null ? null : now() + Math.round(days * D),
  created_ts: now() - createdDays * D, last_seen_ts: seenAgo == null ? 0 : now() - seenAgo,
  note: '', ...extra,
})
const state: {
  members: MockMember[]
  matrix: Record<number, MatrixAccount[]>
  codes: MockCode[]
  inbounds: Inbound[]
  sync: SyncState
  keys: Record<KeyId, MockKey>
  trialOn?: boolean
} = {
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
    xui: { source: 'page', masked: 'x7Kq…93Fa', check: { ok: true, error: '' } },
  },
}
const noteCrypto = () => tr('n8n хранит свою копию токена крипто-бота: после смены обнови токен в его Telegram-credential в n8n.', 'n8n keeps its own copy of the crypto bot token: after changing it, update the token in its Telegram credential in n8n.')
const keyItem = (id: KeyId): KeyItem => {
  const k = state.keys[id]
  return {
    id, kind: id === 'xui' ? 'api' : 'bot', configured: k.source !== 'none', source: k.source, masked: k.masked,
    updated_ts: k.source === 'page' ? now() - 3600 : null, restart_on_change: id === 'helper' || id === 'member',
    ...(k.bot !== undefined ? { bot: k.bot ? { ...k.bot, error: k.bot.error ? tr('api.telegram.org: таймаут', 'api.telegram.org: timeout') : '' } : k.bot } : {}),
    ...(k.check ? { check: { ...k.check, detail: k.check.ok ? tr('25 инбаундов', '25 inbounds') : '' } } : {}),
    ...(id === 'crypto' ? { note: noteCrypto() } : {}),
  }
}
const pickSelf = (r: string | null): TgUser | null => (r === 'stranger' ? null : (r === 'owner' ? MOCK_USERS.owner : r === 'expired' ? MOCK_USERS.expired : MOCK_USERS.member))
const userFor = (r: string | null): TgUser => MOCK_USERS[r as MockRole] || MOCK_USERS.member
let SELF = pickSelf(ROLE)
let TG_USER = userFor(ROLE)
// Owner preview: act as 'stranger' | 'member' | 'expired'; configure(null) goes back to the URL's role.
export function configure(role: string | null, lang?: { pref: LangPref; lang: Lang }): void {
  if (role && lang) { realPref ??= { langPref, autoLang }; langPref = lang.pref || 'auto'; autoLang = lang.lang }
  else if (!role && realPref) { ({ langPref, autoLang } = realPref); realPref = null }
  previewOn = !!role
  ROLE = role || P.get('mock')
  SELF = pickSelf(ROLE)
  TG_USER = userFor(ROLE)
}
let pendingOnce = P.get('pending') === '1'

const find = (id: number | string | undefined) => state.members.find((m) => m.id === Number(id))
const statusOf = (m: MockMember): MemberStatus => (m.suspended ? 'suspended' : m.expires_ts != null && m.expires_ts <= now() ? 'expired' : 'active')
const rnd = (id: number, k: number): number => { const x = Math.sin(id * 12.9898 + k * 78.233) * 43758.5453; return x - Math.floor(x) }

function view(m: MockMember) {
  const st = statusOf(m)
  return {
    id: m.id, username: m.username, first_name: m.first_name, last_name: m.last_name, lang: m.lang,
    status: st, expires_ts: m.expires_ts,
    days_left: m.expires_ts == null ? null : st === 'expired' ? 0 : Math.ceil((m.expires_ts - now()) / D),
    services: m.services, effective_services: effectiveOf(m), created_ts: m.created_ts, last_seen_ts: m.last_seen_ts, note: m.note,
    vpn_email: 'mh-' + m.id, has_vpn_client: m.services.includes('vpn'),
    matrix: (state.matrix[m.id] || []).map((a) => a.mxid),
  }
}
const traffic = (m: MockMember) => (m.services.includes('vpn')
  ? { up: Math.round(rnd(m.id, 1) * 3.2e9), down: Math.round(rnd(m.id, 2) * 4.5e10) } : null)
const online = (m: MockMember): boolean => m.services.includes('vpn') && rnd(m.id, 3) > 0.6 && statusOf(m) === 'active'

function codeView(c: MockCode): AccessCode {
  const state_: AccessCode['state'] = c.revoked ? 'revoked' : c.uses >= c.uses_max ? 'used' : c.valid_until <= now() ? 'expired' : 'live'
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

function botAvatar(h: number): string {
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><rect width="80" height="80" fill="hsl(${h} 60% 45%)"/><rect x="20" y="26" width="40" height="32" rx="10" fill="#fff" fill-opacity=".9"/><circle cx="32" cy="42" r="4" fill="hsl(${h} 60% 45%)"/><circle cx="48" cy="42" r="4" fill="hsl(${h} 60% 45%)"/><rect x="38" y="16" width="4" height="10" fill="#fff" fill-opacity=".9"/></svg>`
  return 'data:image/svg+xml,' + encodeURIComponent(svg)
}

// Admin switches (GET /api/admin/services). `grant` = per-member grant behind a master switch,
// `all` = every active member while on.
const SVC_STATE: { id: string; mode: 'grant' | 'all'; enabled: boolean }[] = [
  { id: 'vpn', mode: 'grant', enabled: true },
  { id: 'matrix', mode: 'grant', enabled: true },
  { id: 'tools', mode: 'grant', enabled: true },
  { id: 'youtube', mode: 'all', enabled: true },
  { id: 'books', mode: 'all', enabled: true },
  { id: 'upscale', mode: 'all', enabled: true },
]
const effectiveOf = (m: MockMember): string[] => SVC_STATE.filter((x) => x.enabled && (x.mode === 'all' || m.services.includes(x.id))).map((x) => x.id)
const SERVICES: ({ id: string } & Record<Lang, [name: string, description: string]>)[] = [
  { id: 'vpn', ru: ['VPN', 'Защищённый доступ в интернет'], en: ['VPN', 'Secure internet access'] },
  { id: 'matrix', ru: ['Мессенджер', 'Приватный чат Matrix'], en: ['Messenger', 'Private Matrix chat'] },
  { id: 'tools', ru: ['Инструменты бота', 'Проверки, ссылки, скачивание видео'], en: ['Bot tools', 'Checks, links, video downloads'] },
  { id: 'youtube', ru: ['Скачивание видео', 'YouTube, RuTube, VK Видео — файлом на телефон'], en: ['Video downloads', 'YouTube, RuTube and VK Video as a file'] },
  { id: 'books', ru: ['Аудиокниги (бета)', 'Озвучка книг EPUB и PDF голосом на выбор'], en: ['Audiobooks (beta)', 'EPUB and PDF books read aloud in a voice you pick'] },
  { id: 'upscale', ru: ['Улучшение фото', 'Увеличение фото и картинок в 2–4 раза нейросетью'], en: ['Photo upscaling', 'Enlarge photos and pictures 2–4× with AI'] },
]
const adminServices = (): AdminService[] => SVC_STATE.map((x) => {
  const sv = SERVICES.find((y) => y.id === x.id)!
  const n = state.members.filter((m) => statusOf(m) === 'active' && (x.mode === 'all' || m.services.includes(x.id))).length
  return { id: x.id, name: sv[L()][0], description: sv[L()][1], mode: x.mode, enabled: x.enabled, members_with_access: n }
})
const AWG_CONF = `[Interface]
PrivateKey = yAnz5TF+lXXJte14tji3zlMNq+hd2rYUIgJBgB3fBmk=
Address = 10.8.2.7/32
DNS = 1.1.1.1, 1.0.0.1
MTU = 1280
Jc = 5
Jmin = 10
Jmax = 50
S1 = 63
S2 = 124
S3 = 17
S4 = 9
H1 = 1884231045
H2 = 2217509884
H3 = 3098114756
H4 = 3917640215

[Peer]
PublicKey = xTIBA5rboUvnH4htodjb6e697QjLERt1NAB4mZqp8Dg=
PresharedKey = 31aIhAPwktDGpH4JDhA8GNvjFXEf/a6+UaQRxLHLMo0=
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = ${SERVER}:20443
PersistentKeepalive = 25
`
const AWG_VPN_URL = 'vpn://' + btoa(JSON.stringify({ containers: [{ container: 'amnezia-awg', awg: { config: AWG_CONF, port: '20443', transport_proto: 'udp' } }], defaultContainer: 'amnezia-awg', description: 'MeowHub', dns1: '1.1.1.1', dns2: '1.0.0.1', hostName: SERVER })).replace(/=+$/, '')

// ---- downloader (CONTRACT3). Jobs seeded in several states; new ones advance queued -> running -> done by wall clock.
const dlPresets = (): Required<Pick<DlPresets, 'video' | 'audio' | 'extras' | 'limits' | 'sites'>> => ({
  video: [{ id: 'v360', label: '360p' }, { id: 'v720', label: '720p' }, { id: 'v1080', label: '1080p' }, { id: 'vbest', label: tr('Лучшее', 'Best') }],
  audio: [{ id: 'mp3', label: 'MP3' }, { id: 'm4a', label: 'M4A' }, { id: 'opus', label: 'Opus' }],
  extras: { subs: ['ru', 'en'], clip: true, playlist_max: 10 },
  limits: { active: 2, per_hour: 10, per_day: 30, ttl_min: 30 },
  sites: ['YouTube', 'RuTube', tr('VK Видео', 'VK Video')],
})
const presetLabel = (id: string): string => { const p = dlPresets(); return [...p.video, ...p.audio].find((x) => x.id === id)?.label || id }
const MB = 1024 * 1024
const dlFile = (name: string, size: number, kind: string) => ({ name, size, url: 'https://' + SERVER + '/app/dl/' + btoa(unescape(encodeURIComponent(name))).replace(/[=+/]/g, '') + '.mockSig', kind })
const dlJob = (id: number, uid: number, url: string, preset: string, status: DlJob['status'], title: string | null, extra: Partial<MockDlJob> = {}): MockDlJob => ({
  id, uid, url, preset, preset_label: presetLabel(preset), title, status, percent: status === 'done' ? 100 : 0, size: null, eta: null,
  error: '', created_ts: now() - 600, finished_ts: null, expires_ts: null, files: [], ...extra,
})
const dlJobs = [
  dlJob(7, 100200301, 'https://www.youtube.com/watch?v=aqz-KE-bpKQ', 'v1080', 'running', 'Как устроен интернет: от кабеля до браузера — лекция', { percent: 47, size: 612 * MB, eta: 74, created_ts: now() - 90 }),
  dlJob(6, 100200301, 'https://rutube.ru/video/5f3c9a1e2b/', 'v360', 'queued', null, { created_ts: now() - 40 }),
  dlJob(5, 100200301, 'https://vk.com/video-22822305_456239018', 'v720', 'done', 'Обзор новой Tesla Model 3 (2026) — честно после года владения', {
    size: 148 * MB, finished_ts: now() - 300, expires_ts: now() + 25 * 60, created_ts: now() - 420,
    files: [dlFile('Обзор новой Tesla Model 3 (2026).mp4', 148 * MB, 'video'), dlFile('Обзор новой Tesla Model 3 (2026).ru.srt', 62 * 1024, 'subs')],
  }),
  dlJob(4, 100200301, 'https://www.youtube.com/watch?v=5qap5aO4i9A', 'mp3', 'done', 'lofi hip hop radio — beats to relax/study to', {
    size: int(8.2 * MB), finished_ts: now() - 1500, expires_ts: now() + 4 * 60, created_ts: now() - 1700,
    files: [dlFile('lofi hip hop radio - beats to relax_study to.mp3', int(8.2 * MB), 'audio')],
  }),
  dlJob(3, 100200301, 'https://www.youtube.com/watch?v=privatevid01', 'v720', 'error', 'Закрытое видео', {
    err_t: ['Видео недоступно: оно закрыто автором или удалено.', 'The video is unavailable: it is private or was removed by the author.'], finished_ts: now() - 2400, created_ts: now() - 2500,
  }),
]
function int(n: number): number { return Math.round(n) }
// Newly submitted jobs (sim: t0): queued 3 s, then running ~15 s, then done.
function dlAdvance(j: MockDlJob): MockDlJob {
  if (!j.sim) return j
  const e = now() - j.sim
  if (e < 3) { j.status = 'queued'; j.percent = 0 } else if (e < 18) {
    j.status = 'running'; j.percent = Math.min(97, Math.round(((e - 3) / 15) * 100)); j.size = int(j.percent * 1.6 * MB); j.eta = Math.max(1, 18 - e)
  } else if (j.status !== 'done' && j.status !== 'canceled') {
    const audio = dlPresets().audio.some((p) => p.id === j.preset)
    const name = (j.title || 'video') + (audio ? '.' + j.preset : '.mp4')
    Object.assign(j, { status: 'done', percent: 100, size: audio ? 5 * MB : 160 * MB, eta: null, finished_ts: now(), expires_ts: now() + 30 * 60, files: [dlFile(name, audio ? 5 * MB : 160 * MB, audio ? 'audio' : 'video')] })
  }
  return j
}
const dlView = (j: MockDlJob): DlJob => {
  const { uid: _uid, sim: _sim, err_t, ...rest } = dlAdvance(j)
  return { ...rest, preset_label: presetLabel(rest.preset), ...(err_t ? { error: tr(...err_t) } : {}) }
}
const mineJobs = () => dlJobs.filter((j) => j.uid === SELF?.id || ROLE === 'owner').sort((a, b) => b.id - a.id)
const dlAvailable = (): boolean => { const m = selfMember(); return ROLE === 'owner' || (!!m && statusOf(m) === 'active' && effectiveOf(m).includes('youtube')) }
const err = (status: number, error: string, message: string): never => { throw new ApiError(status, error, message) }
const selfMember = () => (SELF ? find(SELF.id) : null)
const hasSvc = (s: string): boolean => { const m = selfMember(); return !!m && statusOf(m) === 'active' && m.services.includes(s) }
const ownerOnly = () => { if (ROLE !== 'owner') err(403, 'forbidden', tr('Только для владельца', 'Owner only')) }

export async function handle(method: string, path: string, rawBody?: unknown): Promise<unknown> {
  const body = (rawBody ?? {}) as MockBody   // a server parses JSON; the screens send these shapes
  await delay(180 + Math.random() * 160)
  const failPart = P.get('fail')
  if (failPart && path.includes(failPart)) err(500, 'boom', tr('Внутренняя ошибка сервера (mock)', 'Internal server error (mock)'))
  const seg = (path.split('?')[0] ?? '').split('/')

  if (path === 'me') {
    const m = selfMember()
    // ?via=member: the owner opened the app through the member bot - plain member/stranger.
    const viaMember = !previewOn && ROLE === 'owner' && P.get('via') === 'member'
    return {
      role: ROLE === 'owner' && !viaMember ? 'owner' : m ? 'member' : 'stranger',
      mode: P.get('mode') === 'tg' ? 'tg' : 'browser',
      user: { ...TG_USER, language_code: autoLang },
      lang: L(), lang_pref: langPref,
      member: m ? view(m) : null, contact: '@owner', bot_username: 'ExampleBot',
      via: viaMember ? 'member' : P.get('mode') === 'tg' ? 'helper' : 'browser',
      member_bot_username: state.keys.member.bot?.username || '',
      can_preview: ROLE === 'owner',
      trial: { state: !m && P.get('trial') !== 'used' ? 'ok' : 'used', days: 1 },
      pay: P.get('mode') === 'tg' || previewOn ? payOffer() : null,
      // Only switched-on services; `available` = what this person can actually use right now.
      services: SERVICES.filter((s) => SVC_STATE.find((x) => x.id === s.id)?.enabled).map((s) => ({
        id: s.id, name: s[L()][0], description: s[L()][1],
        available: ROLE === 'owner' && !viaMember ? true : !!m && statusOf(m) === 'active' && effectiveOf(m).includes(s.id),
      })),
    }
  }

  if (path === 'lang' && method === 'POST') {
    if (body.lang !== 'auto' && body.lang !== 'ru' && body.lang !== 'en') return err(400, 'bad_request', tr('Неверный язык', 'Invalid language'))
    langPref = body.lang
    return { lang: L(), lang_pref: langPref }
  }

  if (path === 'trial' && method === 'POST') {
    if (selfMember()) return { result: 'used', message: tr('Пробный период уже использован.', 'The free trial has already been used.'), member: null }
    const m = U(TG_USER.id, TG_USER.first_name, TG_USER.last_name ?? '', TG_USER.username ?? '', 1, ['vpn', 'matrix'], 0, 0)
    state.members.unshift(m)
    return { result: 'new', member: view(m) }
  }
  if (path === 'admin/trial') {
    ownerOnly()
    if (method === 'POST') state.trialOn = !!body.enabled
    return { enabled: state.trialOn !== false, days: 1, per_day: 20, started: 7 }
  }

  if (path === 'pay' && method === 'POST' && body.provider === 'donate') return { provider: 'stars', link: 'https://t.me/$mockDonation' }
  if (path === 'pay' && method === 'POST') {
    const plan = body.plan as PlanId
    if (!(plan in PLAN_DAYS)) return err(400, 'bad_plan', tr('Такого тарифа нет.', 'There is no such plan.'))
    // Fake invoices: mock-tg's openInvoice "pays" at once, and pay/check then extends the member.
    const m = selfMember()
    if (m) m.expires_ts = Math.max(now(), m.expires_ts || now()) + PLAN_DAYS[plan] * D
    return body.provider === 'crypto' ? { provider: 'crypto', url: 'https://t.me/CryptoBot?start=mock' } : { provider: 'stars', link: 'https://t.me/$mockInvoice' }
  }
  if (path === 'pay/check') { const m = selfMember(); return { paid: 0, member: m ? view(m) : null } }

  if (path === 'redeem') {
    const code = (body.code || '').toUpperCase()
    const tail = code.slice(-4)
    const bad = ({ USED: 'used', EXPR: 'expired_code', REVK: 'revoked_code', RATE: 'rate_limited', SUSP: 'suspended' } as Record<string, string>)[tail]
    if (bad) return { result: bad, member: null }
    if (!code.startsWith('MEOW')) return { result: 'invalid', member: null }
    let m = selfMember()
    if (!m) {
      m = U(TG_USER.id, TG_USER.first_name, TG_USER.last_name ?? '', TG_USER.username ?? '', 30, ['vpn', 'matrix'], 0, 0)
      state.members.unshift(m)
      return { result: 'new', member: view(m) }
    }
    m.expires_ts = Math.max(now(), m.expires_ts || now()) + 30 * D
    m.suspended = 0
    return { result: 'extended', member: view(m) }
  }

  if (path === 'vpn') {
    if (!hasSvc('vpn')) err(403, 'no_access', tr('Нет доступа', 'No access'))
    if (pendingOnce) { pendingOnce = false; err(409, 'pending', tr('Клиент ещё создаётся', 'The client is still being created')) }
    const sub = 'https://' + SERVER + '/sub/k3x9a7fq2mzp81vd'
    const go = (a: string): string => `./go/${a}?u=${encodeURIComponent(sub)}`
    return {
      sub_url: sub,
      links: [
        { name: 'Speed · Vision Reality', url: 'vless://6f2a1c3e-91b4-4d0e-8a57-0c9d1b2e3f40@' + SERVER + ':443?security=reality&flow=xtls-rprx-vision&sni=www.icloud.com#Speed' },
        { name: 'Resistance · XHTTP', url: 'vless://6f2a1c3e-91b4-4d0e-8a57-0c9d1b2e3f40@' + SERVER + ':443?type=xhttp&security=reality&sni=dl.google.com#Resistance' },
        { name: 'Trojan TLS', url: 'trojan://q7Zp2mXk9d@' + SERVER + ':8443?sni=' + SERVER + '#Trojan' },
        { name: 'Shadowsocks 2022', url: 'ss://MjAyMi1ibGFrZTMtYWVzLTI1Ni1nY206a2V5@' + SERVER + ':8388#SS' },
      ],
      groups: [
        { id: 'main', title: tr('VLESS и Shadowsocks', 'VLESS and Shadowsocks'), apps: ['Happ', 'v2RayTun', 'v2rayNG', 'Hiddify'],
          hint: tr('Скопируй → в приложении «+» → «Импорт из буфера».', 'Copy → in the app tap “+” → “Import from clipboard”.'),
          links: [
            { name: 'Speed · Vision Reality', url: 'vless://6f2a1c3e-91b4-4d0e-8a57-0c9d1b2e3f40@' + SERVER + ':443?security=reality&flow=xtls-rprx-vision&sni=www.icloud.com#Speed', action: 'copy' },
            { name: 'Resistance · XHTTP', url: 'vless://6f2a1c3e-91b4-4d0e-8a57-0c9d1b2e3f40@' + SERVER + ':443?type=xhttp&security=reality&sni=dl.google.com#Resistance', action: 'copy' },
            { name: 'Trojan TLS', url: 'trojan://q7Zp2mXk9d@' + SERVER + ':8443?sni=' + SERVER + '#Trojan', action: 'copy' },
            { name: 'Shadowsocks 2022', url: 'ss://MjAyMi1ibGFrZTMtYWVzLTI1Ni1nY206a2V5@' + SERVER + ':8388#SS', action: 'copy' },
          ] },
        { id: 'new', title: tr('🧪 Новые протоколы (тест)', '🧪 New protocols (test)'), apps: ['Happ', 'v2RayTun'],
          hint: tr('Нужна последняя версия Happ или v2RayTun. Скопируй → «+» → «Из буфера».', 'Needs the latest Happ or v2RayTun. Copy → “+” → “From clipboard”.'),
          links: [{ name: 'VLESS Encryption (ML-KEM)', url: 'vless://6f2a1c3e-91b4-4d0e-8a57-0c9d1b2e3f40@' + SERVER + ':2096?encryption=mlkem768x25519plus.native.0rtt.xxxx&type=tcp#🧪 Enc', action: 'copy' }] },
        { id: 'udp', title: 'Hysteria2 (UDP)', apps: ['Happ', 'Hiddify', 'v2RayTun'],
          hint: tr('Если обычные не работают. Скопируй → «+» → «Из буфера».', 'If the regular ones don’t work. Copy → “+” → “From clipboard”.'),
          links: [{ name: 'Hysteria2', url: 'hysteria2://k3x9a7fq2m@' + SERVER + ':4443?sni=' + SERVER + '#Hysteria2', action: 'copy' }] },
        { id: 'awg', title: 'AmneziaWG', apps: ['AmneziaWG', 'AmneziaVPN'],
          hint: tr('AmneziaVPN: кнопка выше, в «Добавить в приложение». AmneziaWG: скачай .conf → «+» → «Импорт из файла» (или QR).', 'AmneziaVPN: use its button above, in “Add to an app”. AmneziaWG: download the .conf → “+” → “Import from file” (or scan the QR).'),
          links: [
            { name: tr('Ключ для AmneziaVPN', 'Key for AmneziaVPN'), url: AWG_VPN_URL, action: 'copy' },
            { name: tr('Файл .conf', '.conf file'), url: 'https://' + SERVER + '/app/dl/Zm9vYmFyLm1vY2std2c.mockSig', action: 'download', file_name: 'meowhub-awg.conf' },
            { name: tr('QR для AmneziaWG', 'QR for AmneziaWG'), action: 'qr', text: AWG_CONF },
          ] },
        { id: 'tg', title: tr('Прокси для Telegram', 'Proxy for Telegram'), apps: ['Telegram'],
          hint: tr('Нажми — Telegram сам предложит включить.', 'Tap it — Telegram offers to switch it on.'),
          links: [{ name: tr('MTProto-прокси', 'MTProto proxy'), url: 'https://t.me/proxy?server=' + SERVER + '&port=9443&secret=ee1f2e3d4c5b6a79880a1b2c3d4e5f6a7b', action: 'telegram' }] },
      ],
      traffic: { up: 1.2e9, down: 18.7e9 }, online: true,
      apps: [
        { id: 'happ', name: 'Happ', platforms: ['iOS', 'Android', 'macOS'], recommended: true, go_url: go('happ') },
        { id: 'hiddify', name: 'Hiddify', platforms: ['iOS', 'Android', 'Windows'], go_url: go('hiddify') },
        { id: 'v2raytun', name: 'v2RayTun', platforms: ['iOS', 'Android'], go_url: go('v2raytun') },
        { id: 'streisand', name: 'Streisand', platforms: ['iOS'], go_url: go('streisand') },
        { id: 'v2rayng', name: 'v2rayNG', platforms: ['Android'], go_url: go('v2rayng') },
        { id: 'amnezia', name: 'AmneziaVPN', platforms: tr('только AmneziaWG · Android, iOS, ПК', 'AmneziaWG only · Android, iOS, desktop'), go_url: 'https://' + SERVER + '/app/dl/b3Blbi1tb2Nr.mockSig' },
      ],
    }
  }

  if (seg[0] === 'books') {
    const b = mockBooks()
    if (path === 'books') return b.view()
    const bk = b.list.find((x) => x.id === Number(seg[1]))
    if (!bk) return err(404, 'not_found', tr('Книга не найдена.', 'Book not found.'))
    if (seg[2] === 'narrate') { bk.status = 'queued'; bk.voice = body.voice ?? null; bk.wait = { ahead: 1, starts_in: 640, takes: 720 }; return bk }
    if (seg[2] === 'cancel') { bk.status = 'cancelled'; return bk }
    if (seg[2] === 'delete') { b.list.splice(b.list.indexOf(bk), 1); return { ok: true } }
  }

  if (seg[0] === 'upscale') {
    const u = mockUpscale()
    if (path === 'upscale') return u.view()
    const j = u.list.find((x) => x.id === Number(seg[1]))
    if (!j) return err(404, 'not_found', tr('Картинка не найдена.', 'Picture not found.'))
    if (seg[2] === 'cancel') { j.status = 'cancelled'; return { ok: true } }
    if (seg[2] === 'delete') { u.list.splice(u.list.indexOf(j), 1); return { ok: true } }
  }

  if (seg[0] === 'dl') {
    if (!dlAvailable()) err(403, 'no_access', tr('Скачивание видео выключено', 'Video downloads are switched off'))
    if (path === 'dl/presets') return dlPresets()
    if (path === 'dl' && method === 'POST') {
      if (!/^https?:\/\/\S+$/i.test(body.url || '')) err(400, 'bad_url', tr('Это не похоже на ссылку на видео.', 'That doesn’t look like a video link.'))
      if (/playlist\?list=/.test(body.url ?? '') && !body.playlist) err(400, 'playlist', tr('Это плейлист. Включи «Плейлист» в дополнительных настройках.', 'This is a playlist. Turn on “Playlist” in the extra options.'))
      if (/limit/.test(body.url ?? '')) err(429, 'limit', tr('Лимит: не больше 2 загрузок одновременно. Дождись окончания текущих.', 'Limit: no more than 2 downloads at a time. Wait for the current ones to finish.'))
      if (/disk/.test(body.url ?? '')) err(503, 'disk_full', tr('На сервере кончилось место. Попробуй позже.', 'The server is out of space. Try again later.'))
      const id = Math.max(...dlJobs.map((j) => j.id)) + 1
      const host = (() => { try { return new URL(body.url ?? '').hostname.replace(/^www\./, '') } catch { return body.url ?? '' } })()
      const j = dlJob(id, SELF?.id ?? 0, body.url ?? '', body.preset ?? '', 'queued', tr('Видео с ', 'Video from ') + host + (body.clip ? tr(' (фрагмент)', ' (clip)') : ''), { sim: now(), created_ts: now() })
      dlJobs.push(j)
      return dlView(j)
    }
    if (path === 'dl') return mineJobs().map(dlView)
    const j = dlJobs.find((x) => x.id === Number(seg[1]))
    if (!j) return err(404, 'not_found', tr('Загрузка не найдена', 'Download not found'))
    if (seg[2] === 'cancel') { dlAdvance(j); if (j.status === 'queued' || j.status === 'running') { j.status = 'canceled'; j.sim = 0; j.finished_ts = now() } return dlView(j) }
    if (seg[2] === 'delete') { dlJobs.splice(dlJobs.indexOf(j), 1); return { ok: true } }
  }

  if (path === 'matrix') {
    if (!hasSvc('matrix')) err(403, 'no_access', tr('Нет доступа', 'No access'))
    const accounts = state.matrix[SELF?.id ?? 0] || []
    return { server_name: SERVER, client_url: 'https://matrix.' + SERVER, element_url: 'https://app.element.io/#/welcome', accounts, max: 2, can_create: accounts.length < 2 }
  }
  if (path === 'matrix/create') {
    if (!hasSvc('matrix')) err(403, 'no_access', tr('Нет доступа', 'No access'))
    const username = body.username ?? '', password = body.password ?? ''
    if (!/^[a-z0-9._=-]{3,24}$/.test(username)) err(400, 'bad_username', tr('Недопустимое имя', 'Invalid username'))
    if (password.length < 10) err(400, 'weak_password', tr('Слишком короткий пароль', 'Password is too short'))
    if (['admin', 'taken'].includes(username)) err(409, 'taken', tr('Имя занято', 'Username is taken'))
    const list = (state.matrix[SELF?.id ?? 0] ||= [])
    if (list.length >= 2) err(400, 'limit', tr('Лимит аккаунтов', 'Account limit reached'))
    const mxid = `@${username}:${SERVER}`
    list.push({ mxid, created_ts: now(), locked: false })
    return { mxid }
  }
  if (path === 'matrix/password') {
    if ((body.password ?? '').length < 10) err(400, 'weak_password', tr('Слишком короткий пароль', 'Password is too short'))
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
    if (rest[0] === 'services') {
      if (rest[1] && method === 'POST') {
        const x = SVC_STATE.find((v) => v.id === rest[1])
        if (!x) return err(404, 'not_found', tr('Неизвестный сервис', 'Unknown service'))
        if (typeof body.enabled !== 'boolean') return err(400, 'bad_request', tr('Нужно true или false', 'Expected true or false'))
        x.enabled = body.enabled
      }
      return adminServices()
    }
    if (rest[0] === 'dl') {
      const NAMES: Record<number, string> = { 100200301: 'Алексей Громов', 100200302: 'Мария Соколова', 100200307: 'Сергей Новиков', 100200300: 'Админ' }
      const extra = [
        dlJob(12, 100200302, 'https://rutube.ru/video/aa11/', 'v720', 'done', 'Кулинарный мастер-класс: бешбармак', { size: 212 * MB, created_ts: now() - 3 * 3600 }),
        dlJob(11, 100200307, 'https://www.youtube.com/watch?v=jNQXAC9IVRw', 'vbest', 'done', 'Me at the zoo', { size: 3 * MB, created_ts: now() - 5 * 3600 }),
        dlJob(10, 100200300, 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'm4a', 'expired', 'Rick Astley — Never Gonna Give You Up', { size: 4 * MB, created_ts: now() - 9 * 3600 }),
        dlJob(9, 100200302, 'https://vk.com/video-1_1', 'v1080', 'error', 'VK клип', { err_t: ['HTTP 403: доступ запрещён', 'HTTP 403: access denied'], created_ts: now() - 11 * 3600 }),
        dlJob(8, 100200307, 'https://www.youtube.com/watch?v=lecture', 'mp3', 'canceled', 'Лекция по сетям, часть 2', { created_ts: now() - 20 * 3600 }),
      ]
      const all = [...dlJobs, ...extra].map((j) => ({ ...dlView(j), user: NAMES[j.uid] || String(j.uid), uid: j.uid })).sort((x, y) => y.created_ts - x.created_ts)
      return { disk_free_gb: 412, active: all.filter((j) => j.status === 'running' || j.status === 'queued').length, today: { jobs: all.length + 2, bytes: all.reduce((a, j) => a + (j.size || 0), 0) }, jobs: all }
    }
    if (rest[0] === 'payments') {
      if (method === 'POST') {
        if (body.provider === 'donate') {
          if (typeof body.enabled === 'boolean') PAY.donate.enabled = body.enabled
          if (body.amounts) PAY.donate.amounts = [...body.amounts].sort((a, b) => a - b)
          return adminPay()
        }
        const p = body.provider as PayProvider
        if (!(p in PAY)) return err(404, 'unknown_provider', tr('Такого способа оплаты нет.', 'There is no such payment method.'))
        if (typeof body.enabled === 'boolean') PAY[p].enabled = body.enabled
        if (body.prices) Object.assign(PAY[p].prices, body.prices)
      }
      return adminPay()
    }
    if (rest[0] === 'sync') { state.sync = { ts: now(), ok: true, error: '', vpn_clients: state.members.filter((m) => m.services.includes('vpn')).length }; return state.sync }
    if (rest[0] === 'integrations') {
      await delay(300)
      if (rest.length === 1) return { items: (['helper', 'member', 'crypto', 'xui'] as KeyId[]).map(keyItem) }
      const id = rest[1] as KeyId, k = state.keys[id]
      if (!k) return err(404, 'not_found', tr('Неизвестный ключ', 'Unknown key'))
      if (rest[2] === 'reset') {
        if (k.source !== 'page') err(404, 'no_override', tr('Нет ключа, заданного здесь', 'No key set here'))
        k.source = id === 'member' ? 'none' : 'env'; k.masked = id === 'member' ? '' : k.masked
        if (id === 'member') k.bot = null
        return { item: keyItem(id), restarting: id === 'helper' || id === 'member' }
      }
      const tok = String(body.token || '').trim()
      if (tok.length < 8) err(400, 'bad_token', tr('Telegram ответил: Unauthorized — токен не подошёл.', 'Telegram answered: Unauthorized — the token was rejected.'))
      if (tok.includes('bad')) err(400, 'bad_token', tr('Telegram ответил: Unauthorized — токен не подошёл.', 'Telegram answered: Unauthorized — the token was rejected.'))
      if (id === 'member' && tok.includes('same')) err(400, 'same_bot', tr('Это тот же бот, что и служебный. Нужен отдельный бот от @BotFather.', 'That is the same bot as the service one. You need a separate bot from @BotFather.'))
      if (id === 'crypto' && tok.includes('apply')) err(502, 'crypto_apply_failed', tr('Крипто-сервис не принял токен (502). Ничего не сохранено.', 'The crypto service rejected the token (502). Nothing was saved.'))
      k.source = 'page'; k.masked = tok.slice(0, 4) + '…' + tok.slice(-4)
      if (id === 'xui') k.check = { ok: true, error: '' }
      else k.bot = { id: 9000 + id.length, username: id === 'member' ? 'ExampleMemberBot' : 'NewBot', name: id === 'member' ? 'MeowHub' : 'New Bot', ok: true, error: '' }
      return { item: keyItem(id), restarting: id === 'helper' || id === 'member' }
    }
    if (rest[0] === 'inbounds') {
      if (method === 'POST') {
        const ids = body.member_ids ?? []
        const awg = state.inbounds.filter((i) => ids.includes(i.id) && ['wireguard', 'amneziawg'].includes(i.protocol))
        if (awg.length > 1) err(400, 'two_awg', tr('Нельзя выдавать два AmneziaWG/WireGuard инбаунда одновременно', 'You can’t assign two AmneziaWG/WireGuard inbounds at once'))
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
      if (!m) return err(404, 'not_found', tr('Участник не найден', 'Member not found'))
      if (method === 'POST') {
        const a = body.action
        if (a === 'extend') m.expires_ts = Math.max(now(), m.expires_ts || now()) + (body.days ?? 0) * D
        else if (a === 'set_expiry') m.expires_ts = body.ts ?? null
        else if (a === 'suspend') m.suspended = 1
        else if (a === 'resume') m.suspended = 0
        else if (a === 'services') m.services = body.services ?? []
        else if (a === 'note') m.note = body.note ?? ''
        else if (a === 'delete') { state.members = state.members.filter((x) => x !== m); return { deleted: true } }
        return view(m)
      }
      const ev = (k: number, d: string, ago: number, detail = '') => ({ id: k, ts: now() - ago * D, uid: m.id, kind: d, detail })
      return {
        ...view(m), traffic: traffic(m), online: online(m), vpn_links_count: m.services.includes('vpn') ? 4 : 0,
        matrix_accounts: state.matrix[m.id] || [],
        events: [
          ev(5, 'extend', 1.2, tr('+30 дн.', '+30 d')), ev(4, 'redeem', 31, 'MEOW-X8LD-2FGJ · 30 дн.'),
          ev(3, 'matrix_create', 30, '@' + (m.username || 'user') + ':' + SERVER), ev(2, 'services', 40, 'vpn, matrix'),
          ev(1, 'join', (Date.now() / 1000 - m.created_ts) / D, tr('Первый вход', 'First sign-in')),
        ],
      }
    }
    if (rest[0] === 'grant') {
      const days = body.days ?? 0, services = body.services ?? []
      let m = find(body.uid)
      if (!m) { m = U(body.uid ?? 0, tr('Новый', 'New'), tr('участник', 'member'), '', days, services, null, 0); state.members.unshift(m) } else {
        m.expires_ts = Math.max(now(), m.expires_ts || now()) + days * D
        m.services = [...new Set([...m.services, ...services])]
      }
      return view(m)
    }
    if (rest[0] === 'codes') {
      if (rest[2] === 'revoke') { const c = state.codes.find((x) => x.code === rest[1]); if (c) c.revoked = 1; return { ok: true } }
      if (method === 'POST') {
        const code = 'MEOW-' + Array.from({ length: 2 }, () => Array.from({ length: 4 }, () => 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'[Math.floor(Math.random() * 32)]).join('')).join('-')
        state.codes.unshift({ code, days: body.days ?? 30, services: body.services ?? [], uses_max: body.uses_max ?? 1, uses: 0, created_ts: now(), valid_until: now() + 30 * D, revoked: 0, note: body.note || '' })
        return { code, share_url: 'https://t.me/ExampleBot?start=' + code }
      }
      return state.codes.map(codeView)
    }
  }
  return err(404, 'not_found', tr('Не найдено: ', 'Not found: ') + path)
}

function adminPay(): PaymentsAdmin {
  const ago = (h: number) => now() - h * 3600
  return {
    settings: PAY, crypto_token: true, crypto_testnet: false,
    plans: (Object.keys(PLAN_DAYS) as PlanId[]).map((id) => ({ id, days: PLAN_DAYS[id] })),
    totals: { stars: { count: 3, amount: 470 }, crypto: { count: 1, amount: 5 }, donate: { count: 2, amount: 350 } },
    recent: [
      { id: 'stars:a', uid: 100200301, name: 'Алексей Громов', provider: 'stars', plan: 'm1', days: 30, amount: '100', ts: ago(3), refunded: 0 },
      { id: 'crypto:7', uid: 100200302, name: 'Мария Соколова', provider: 'crypto', plan: 'm3', days: 90, amount: '5.00', ts: ago(30), refunded: 0 },
      { id: 'stars:b', uid: 100200309, name: 'Никита Фёдоров', provider: 'stars', plan: 'm3', days: 90, amount: '270', ts: ago(80), refunded: 0 },
      { id: 'stars:c', uid: 100200311, name: 'Артём Кузнецов', provider: 'stars', plan: 'm1', days: 30, amount: '100', ts: ago(200), refunded: 1 },
    ],
  }
}

// ---- photo upscaling: a finished picture (drawn SVGs: blocky "before", smooth "after") and one in line
let UPSCALE: { list: UpscaleJob[]; view: () => UpscaleView } | null = null
const svgPic = (smooth: boolean): string => 'data:image/svg+xml,' + encodeURIComponent(
  `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 48" ${smooth ? '' : 'shape-rendering="crispEdges"'}>` +
  '<rect width="64" height="48" fill="#8fc6e8"/><circle cx="50" cy="11" r="6" fill="#ffd23f"/>' +
  '<path d="M0 34 18 18l12 10 10-8 24 18v10H0z" fill="#3e7d5a"/><ellipse cx="24" cy="38" rx="9" ry="5" fill="#f6d26b"/>' +
  `<circle cx="30" cy="31" r="4" fill="#f6d26b"/><path d="M33 31h4l-4 1.6z" fill="#ff9a3d"/></svg>`)
function mockUpscale() {
  if (!UPSCALE) {
    const list: UpscaleJob[] = [

        { id: 2, name: 'IMG_2041', preset: 'photo', status: 'running', progress: 46, scale: 4, scale_asked: 4, format: 'jpg', in_w: 1024, in_h: 768, out_w: 4096, out_h: 3072, wait: { ahead: 0, ready_in: 9 }, demo: { before: svgPic(false) } },
        { id: 1, name: 'pond-at-dusk', preset: 'detail', status: 'done', progress: 100, scale: 4, scale_asked: 4, format: 'jpg', in_w: 640, in_h: 480, out_w: 2560, out_h: 1920, out_size: 1.9e6, took: 6, view_url: '', save_url: '', demo: { before: svgPic(false), after: svgPic(true) } },
    ]
    UPSCALE = { list, view: () => ({ jobs: list, presets: ['photo', 'detail', 'soft', 'art'], queue: { jobs: 1, wait: 9 }, online: true,
      limits: { per_day: 20, active: 3, max_mb: 25 }, quota: { per_day: 20, used: 2, left: 18, next_in: null, max_mb: 25, unlimited: false } }) }
  }
  return UPSCALE
}

// ---- audiobooks (beta): three demo books in different states
let BOOKS: { list: MemberBook[]; voices: BooksView['voices']; view: () => BooksView } | null = null
function mockBooks() {
  if (!BOOKS) {
    const V = (k: string, n: string, g: 'f' | 'm', a: string): BookVoice => ({ key: 'kokoro:' + k, engine: 'kokoro', id: k, name: n, gender: g, accent: a })
    const list: MemberBook[] = [
        { id: 3, title: 'The Pragmatic Programmer', author: 'David Thomas, Andrew Hunt', lang: 'en', status: 'ready', voice: null, chapters: 41, listen_s: 9.5 * 3600, has_cover: false, takes: { kokoro: 760 } },
        { id: 2, title: 'Designing Data-Intensive Applications', author: 'Martin Kleppmann', lang: 'en', status: 'narrating', voice: 'kokoro:bf_emma', chapters: 12, listen_s: 21 * 3600, percent: 38, eta: 1140, has_cover: false },
        { id: 1, title: 'Clean Architecture', author: 'Robert C. Martin', lang: 'en', status: 'done', voice: 'kokoro:af_heart', chapters: 43, listen_s: 30374, seconds: 30374, size: 262e6, has_cover: false, audio_url: '', save_url: '' },
    ]
    const voices: BooksView['voices'] = { en: [V('af_heart', 'Heart', 'f', 'us'), V('af_bella', 'Bella', 'f', 'us'), V('bf_emma', 'Emma', 'f', 'gb'), V('am_michael', 'Michael', 'm', 'us'), V('am_fenrir', 'Fenrir', 'm', 'us'), V('bm_george', 'George', 'm', 'gb')],
        ru: [{ key: 'silero:xenia', engine: 'silero', id: 'xenia', name: 'Ксения', gender: 'f', accent: '' }, { key: 'silero:aidar', engine: 'silero', id: 'aidar', name: 'Айдар', gender: 'm', accent: '' }] }
    BOOKS = { list, voices, view: () => {
      const used = list.filter((b) => ['done', 'queued', 'narrating', 'assembling'].includes(b.status)).length
      return { books: list, voices, queue: { books: 1, wait: 1140 }, limits: { per_user: 3, max_mb: 100 },
        quota: { per_user: 3, used, left: Math.max(0, 3 - used), max_mb: 100, unlimited: false } }
    } }
  }
  return BOOKS
}
