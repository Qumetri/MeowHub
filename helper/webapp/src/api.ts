import { initData, isTg } from './tg'
import { getLang, t } from './i18n'

// Dev-only mock switch. In a production build import.meta.env.DEV is the
// literal `false`, so this and the dynamic import below are dead code and the
// bundler drops mock.js entirely.
export const MOCK = import.meta.env.DEV ? new URLSearchParams(location.search).get('mock') : null

import type { Lang } from './types'

export class ApiError extends Error {
  status: number
  code: string
  constructor(status: number, code: string, message?: string) {
    super(message || code)
    this.status = status
    this.code = code
  }
}

// Owner "preview as member": App.jsx loads mock.js lazily and installs its
// handler here, so every screen keeps calling api() but is served mock data and
// never touches the server.
export type PreviewApi = (method: string, path: string, body?: unknown) => Promise<unknown>
let previewApi: PreviewApi | null = null
export function setPreviewApi(fn: PreviewApi | null): void { previewApi = fn }
export const inPreview = () => !!previewApi

interface ApiOptions { method?: 'GET' | 'POST'; body?: unknown }

/** T is what the caller expects back; the server is trusted to send that shape (types.ts). */
export async function api<T = unknown>(path: string, { method = 'GET', body }: ApiOptions = {}): Promise<T> {
  if (previewApi) return previewApi(method, path, body) as Promise<T>
  if (import.meta.env.DEV && MOCK) {
    const m = await import('./mock')
    return m.handle(method, path, body) as Promise<T>
  }
  const headers: Record<string, string> = { 'X-Lang': getLang() satisfies Lang }
  if (isTg) headers['X-Tg-Init-Data'] = initData
  // The server's CSRF guard wants a JSON content type on every non-GET, even with an empty body.
  if (method !== 'GET') headers['Content-Type'] = 'application/json'
  let res
  try {
    res = await fetch('./api/' + path, {
      method, headers, body: method !== 'GET' ? JSON.stringify(body ?? {}) : undefined, cache: 'no-store',
    })
  } catch {
    throw new ApiError(0, 'network', t('err.network'))
  }
  let data: { error?: string; message?: string } | null = null
  try { data = await res.json() } catch { /* empty or non-JSON body */ }
  if (!res.ok) {
    const code = data?.error || 'http_' + res.status
    const msg = res.status === 401 ? t('err.unauthorized') : data?.message || t('err.generic')
    throw new ApiError(res.status, code, msg)
  }
  // /api/admin/members flags a degraded VPN panel in X-Warning; ride along on the array.
  const warn = res.headers.get('X-Warning')
  if (warn && Array.isArray(data)) Object.defineProperty(data, 'warning', { value: warn })
  return data as T
}

// The server may hand back avatar_url as "/api/..." or "api/..."; the app lives
// under a secret path prefix, so anything under api/ is made relative.
export function relUrl(u: string): string;
export function relUrl(u: string | null | undefined): string | null | undefined;
export function relUrl(u: string | null | undefined): string | null | undefined {
  if (!u) return u
  if (/^(https?:|data:|blob:|\.\/)/.test(u)) return u
  return './' + u.replace(/^\/+/, '')
}
export function avatarSrc(uid: number): string | null {
  if ((import.meta.env.DEV && MOCK) || previewApi) {
    // Dev mock / preview: a few fake photos, the rest exercise the initials fallback.
    if (uid % 3 === 0) return null
    const h = (Number(uid) * 47) % 360
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${h} 60% 62%)"/><stop offset="1" stop-color="hsl(${(h + 50) % 360} 55% 38%)"/></linearGradient></defs><rect width="80" height="80" fill="url(#g)"/><circle cx="40" cy="31" r="13" fill="#fff" fill-opacity=".85"/><path d="M12 80c3-19 17-26 28-26s25 7 28 26z" fill="#fff" fill-opacity=".85"/></svg>`
    return 'data:image/svg+xml,' + encodeURIComponent(svg)
  }
  return './api/avatar/' + uid
}

// <img src="./api/..."> cannot carry the X-Tg-Init-Data header, so in Telegram the
// server answers 401. Fetch such images with the header and hand back a blob: URL.
const imgCache = new Map<string, Promise<string>>()
export function loadAuthedImage(url: string | null): Promise<string | null> {
  if (!url || !isTg || !/^\.\/api\//.test(url) || previewApi) return Promise.resolve(url)
  if (!imgCache.has(url)) {
    imgCache.set(url, fetch(url, { headers: { 'X-Tg-Init-Data': initData } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error('http ' + r.status))))
      .then((b) => URL.createObjectURL(b))
      .catch((e) => { imgCache.delete(url); throw e }))
  }
  return imgCache.get(url)!
}
