// The video page's anonymous id: POST api/pub/hello hands out a signed token that is kept in
// localStorage (in memory when storage is blocked) and sent as X-Guest by api(). Requests that
// race to renew it share one hello.
import { ApiError, type GuestAuth } from './api'
import { getLang, t } from './i18n'

const KEY = 'mh_guest'
let mem = ''
let pending: Promise<void> | null = null

function read(): string {
  try { return localStorage.getItem(KEY) || mem } catch { return mem }
}

async function hello(): Promise<void> {
  let res
  try {
    res = await fetch('./api/pub/hello', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lang': getLang() }, body: '{}', cache: 'no-store',
    })
  } catch {
    throw new ApiError(0, 'network', t('err.network'))
  }
  let data: { token?: string; error?: string; message?: string } | null = null
  try { data = await res.json() } catch { /* empty or non-JSON body */ }
  if (!res.ok || !data?.token) throw new ApiError(res.status, data?.error || 'http_' + res.status, data?.message || t('err.generic'))
  mem = data.token
  try { localStorage.setItem(KEY, data.token) } catch { /* private mode: memory only */ }
}

export const guestAuth: GuestAuth = {
  token: read,
  renew() {
    pending ??= hello().finally(() => { pending = null })
    return pending
  },
}
