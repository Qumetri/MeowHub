import { getLang, t } from './i18n.js'

export const nowSec = () => Math.floor(Date.now() / 1000)
const loc = () => (getLang() === 'ru' ? 'ru-RU' : 'en-US')

export function fmtDate(ts, withYear) {
  const d = new Date(ts * 1000)
  const y = withYear ?? d.getFullYear() !== new Date().getFullYear()
  return new Intl.DateTimeFormat(loc(), { day: 'numeric', month: 'long', ...(y ? { year: 'numeric' } : {}) }).format(d)
}
export function fmtShortDay(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Intl.DateTimeFormat(loc(), { day: 'numeric', month: 'short' }).format(new Date(y, m - 1, d))
}
export function fmtDateTime(ts) {
  return new Intl.DateTimeFormat(loc(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(new Date(ts * 1000))
}

export function plural(n, forms) {
  n = Math.abs(n)
  if (getLang() === 'ru') {
    const a = n % 10, b = n % 100
    if (a === 1 && b !== 11) return forms[0]
    if (a >= 2 && a <= 4 && (b < 12 || b > 14)) return forms[1]
    return forms[2]
  }
  return n === 1 ? forms[3] ?? forms[0] : forms[4] ?? forms[1]
}
export const daysWord = (n) => plural(n, ['день', 'дня', 'дней', 'day', 'days'])

export function fmtBytes(n) {
  if (n == null) return '—'
  const u = getLang() === 'ru' ? ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ'] : ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
  const v = i === 0 || n >= 100 ? Math.round(n) : n.toFixed(1).replace('.', getLang() === 'ru' ? ',' : '.')
  return `${v} ${u[i]}`
}

export function ago(ts) {
  if (!ts) return getLang() === 'ru' ? 'не заходил(а)' : 'never seen'
  const s = Math.max(0, nowSec() - ts)
  const ru = getLang() === 'ru'
  let v
  if (s < 90) v = ru ? 'только что' : 'just now'
  else if (s < 3600) v = ru ? `${Math.round(s / 60)} мин назад` : `${Math.round(s / 60)} min ago`
  else if (s < 86400) v = ru ? `${Math.round(s / 3600)} ч назад` : `${Math.round(s / 3600)} h ago`
  else if (s < 86400 * 60) v = ru ? `${Math.round(s / 86400)} дн назад` : `${Math.round(s / 86400)} d ago`
  else v = fmtDate(ts, true)
  return ru ? `был(а) ${v}` : `seen ${v}`
}

export function personName(m) {
  if (!m) return ''
  const n = [m.first_name, m.last_name].filter(Boolean).join(' ')
  return n || (m.username ? '@' + m.username : String(m.id))
}
export function initials(m) {
  const base = [m.first_name, m.last_name].filter(Boolean).map((s) => s[0])
  const s = (base.length ? base.join('') : (m.username || '?')[0]).slice(0, 2)
  return s.toUpperCase()
}
export const hueOf = (id) => (Number(id) * 137.508) % 360

// Effective state for UI: 'active' | 'soon' (<=3 d) | 'expired' | 'suspended'
export function uiState(m) {
  if (!m) return 'expired'
  if (m.status === 'suspended') return 'suspended'
  if (m.status === 'expired') return 'expired'
  if (m.days_left != null && m.days_left <= 3) return 'soon'
  return 'active'
}
export const statusLabel = (m) => {
  const s = uiState(m)
  return s === 'soon' ? t('status.soon') : t('status.' + s)
}

// Server-side effective_services (grant + switched-on "all" services) wins over the raw grants.
export const svcList = (m) => m?.effective_services ?? m?.services ?? []
export function hasService(m, id) {
  return !!m && m.status === 'active' && svcList(m).includes(id)
}
// Downloads: the owner always has them, a member when /api/me marks youtube available.
export function canDownload(me) {
  return !!me && (me.role === 'owner' || !!me.services?.find((s) => s.id === 'youtube')?.available)
}

// Seconds -> "45 с" / "3 мин" / "1 ч 05 мин".
export function fmtDur(sec) {
  const ru = getLang() === 'ru'
  sec = Math.max(0, Math.round(sec))
  if (sec < 60) return `${sec} ${ru ? 'с' : 's'}`
  if (sec < 3600) return `${Math.round(sec / 60)} ${ru ? 'мин' : 'min'}`
  return `${Math.floor(sec / 3600)} ${ru ? 'ч' : 'h'} ${String(Math.round((sec % 3600) / 60)).padStart(2, '0')} ${ru ? 'мин' : 'min'}`
}
// "1:23" / "83" / "1:02:03" -> seconds; '' -> null; garbage -> NaN.
export function parseClock(s) {
  s = (s || '').trim()
  if (!s) return null
  if (!/^\d{1,3}(:\d{1,2}){0,2}$/.test(s)) return NaN
  return s.split(':').reduce((a, p) => a * 60 + Number(p), 0)
}

export function shortUrl(u, n = 34) {
  return u.length <= n ? u : u.slice(0, Math.ceil(n * 0.62)) + '…' + u.slice(-Math.floor(n * 0.3))
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch { /* fall through */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0'
    document.body.appendChild(ta)
    ta.focus(); ta.select()
    const ok = document.execCommand('copy')
    ta.remove()
    return ok
  } catch { return false }
}

export const normCode = (s) => (s || '').toUpperCase().replace(/[^A-Z0-9]/g, '')
