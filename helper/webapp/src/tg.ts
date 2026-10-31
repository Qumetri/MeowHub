// Thin wrapper over Telegram.WebApp. Everything degrades to a no-op (or a
// plain-browser equivalent) outside Telegram, so screens never branch on it.
import type { TgUser } from './types'

export type InvoiceStatus = 'paid' | 'cancelled' | 'failed' | 'pending'
type Haptic = 'light' | 'medium' | 'heavy' | 'rigid' | 'soft'
type Notice = 'success' | 'error' | 'warning'
/** The parts of Telegram.WebApp this app touches (telegram-web-app.js, loaded in index.html). */
export interface TgWebApp {
  initData: string
  initDataUnsafe?: { user?: TgUser }
  colorScheme?: 'light' | 'dark'
  onEvent?(event: string, cb: () => void): void
  ready(): void
  expand(): void
  setHeaderColor?(color: string): void
  setBackgroundColor?(color: string): void
  isVersionAtLeast?(version: string): boolean
  HapticFeedback: { impactOccurred(s: Haptic): void; notificationOccurred(s: Notice): void; selectionChanged(): void }
  showConfirm?(message: string, cb: (ok: boolean) => void): void
  openLink(url: string): void
  openTelegramLink(url: string): void
  downloadFile?(p: { url: string; file_name: string }): void
  readTextFromClipboard?(cb: (text: string | null) => void): void
  openInvoice?(url: string, cb: (status: InvoiceStatus) => void): void
  BackButton: { show(): void; hide(): void; onClick(cb: () => void): void }
  MainButton: {
    setParams(p: { text?: string; is_visible?: boolean; is_active?: boolean }): void
    showProgress(leaveActive?: boolean): void
    hideProgress(): void
    onClick(cb: () => void): void
    offClick(cb: () => void): void
    hide(): void
  }
}
declare global {
  interface Window { Telegram?: { WebApp?: TgWebApp } }
}

const W = (): TgWebApp | undefined => window.Telegram?.WebApp
/** Only called on paths guarded by isTg, where the WebApp object exists. */
const TW = (): TgWebApp => W()!

export const initData = W()?.initData || ''
export const isTg = !!initData
export const MODE: 'tg' | 'browser' = isTg ? 'tg' : 'browser'
export const tgUser: TgUser | null = W()?.initDataUnsafe?.user || null

export function startTg() {
  document.documentElement.dataset.mode = MODE
  if (!isTg) return
  const w = TW()
  const apply = () => { document.documentElement.dataset.scheme = w.colorScheme || 'light' }
  apply()
  try { w.onEvent?.('themeChanged', apply) } catch { /* old client */ }
  try { w.ready() } catch { /* ignore */ }
  try { w.expand() } catch { /* ignore */ }
  try { w.setHeaderColor?.('secondary_bg_color') } catch { /* old client */ }
  try { w.setBackgroundColor?.('secondary_bg_color') } catch { /* old client */ }
}

const safe = (fn: () => void): void => { try { fn() } catch { /* unsupported on this client */ } }
export const haptic = {
  impact: (s: Haptic = 'light') => { if (isTg) safe(() => TW().HapticFeedback.impactOccurred(s)) },
  notify: (s: Notice = 'success') => { if (isTg) safe(() => TW().HapticFeedback.notificationOccurred(s)) },
  select: () => { if (isTg) safe(() => TW().HapticFeedback.selectionChanged()) },
}

export function confirmDialog(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    const confirm = isTg ? TW().showConfirm : undefined
    if (confirm) {
      try { confirm.call(TW(), message, (ok) => resolve(!!ok)); return } catch { /* fall back */ }
    }
    resolve(window.confirm(message))
  })
}

export function openLink(url: string): void {
  if (isTg) { try { TW().openLink(url); return } catch { /* fall back */ } }
  window.open(url, '_blank', 'noopener')
}

// Save a file to the device. Telegram 8.0+ has a native downloader; older
// clients hand the URL to the system browser. Outside Telegram: a plain <a download>.
export function saveFile(url: string, fileName?: string): void {
  if (isTg) {
    const w = TW()
    try {
      if (w.isVersionAtLeast?.('8.0') && w.downloadFile) { w.downloadFile({ url, file_name: fileName || 'file' }); return }
    } catch { /* fall back */ }
    openLink(url)
    return
  }
  const a = document.createElement('a')
  a.href = url
  if (fileName) a.download = fileName
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

// Clipboard read. Telegram's readTextFromClipboard (6.4+) answers only Mini Apps
// launched from the attachment menu -- ours (menu button / inline button) get null --
// and the web API is allowed in some WebViews (iOS shows a "Paste" bubble) and denied
// in others (most Android). So both start at once, inside the tap's user activation,
// and the first non-empty text wins; null when neither gives anything (the caller
// then focuses the field so the keyboard's own paste/clipboard chip can be used).
export const canReadClipboard = () => !!(
  (isTg && TW().readTextFromClipboard && TW().isVersionAtLeast?.('6.4')) || navigator.clipboard?.readText || isTg)
export function readClipboard(): Promise<string | null> {
  return new Promise((resolve) => {
    let pending = 0, done = false
    const finish = (txt: string | null) => {
      if (done) return
      if (txt && String(txt).trim()) { done = true; resolve(String(txt)); return }
      if (--pending <= 0) { done = true; resolve(null) }
    }
    if (navigator.clipboard?.readText) {
      pending++
      try { navigator.clipboard.readText().then(finish, () => finish(null)) } catch { finish(null) }
    }
    const tgRead = isTg && TW().isVersionAtLeast?.('6.4') ? TW().readTextFromClipboard : undefined
    if (tgRead) {
      pending++
      try { tgRead.call(TW(), (txt) => finish(txt)) } catch { finish(null) }
    }
    if (!pending) { resolve(null); return }
    setTimeout(() => { if (!done) { done = true; resolve(null) } }, 1500)   // a callback that never comes
  })
}

/** Telegram's own payment sheet (Stars). Resolves with how it closed; 'failed' outside Telegram. */
export function openInvoice(url: string): Promise<InvoiceStatus> {
  return new Promise((resolve) => {
    const open = isTg ? TW().openInvoice : undefined
    if (!open) { resolve('failed'); return }
    try { open.call(TW(), url, (status) => resolve(status)) } catch { resolve('failed') }
  })
}

export function openTelegramLink(url: string): void {
  if (isTg) { try { TW().openTelegramLink(url); return } catch { /* fall back */ } }
  window.open(url, '_blank', 'noopener')
}

// ---- BackButton: a stack of handlers; visible while the stack is non-empty.
const backStack: (() => void)[] = []
let backWired = false
function syncBack() {
  const bb = W()?.BackButton
  if (!bb) return
  if (backStack.length) bb.show(); else bb.hide()
}
export function pushBack(fn: () => void): () => void {
  if (!isTg) return () => {}
  const bb = TW().BackButton
  if (!backWired) { bb.onClick(() => backStack[backStack.length - 1]?.()); backWired = true }
  backStack.push(fn)
  syncBack()
  return () => {
    const i = backStack.lastIndexOf(fn)
    if (i >= 0) backStack.splice(i, 1)
    syncBack()
  }
}

// ---- MainButton
export function mainButton({ text, enabled, loading, onClick }: { text: string; enabled: boolean; loading: boolean; onClick: () => void }): () => void {
  const mb = TW().MainButton
  mb.setParams({ text, is_visible: true, is_active: !!enabled })
  if (loading) mb.showProgress(false); else mb.hideProgress()
  mb.onClick(onClick)
  return () => { mb.offClick(onClick); mb.hide(); mb.hideProgress() }
}
