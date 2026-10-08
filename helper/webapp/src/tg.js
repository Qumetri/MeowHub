// Thin wrapper over Telegram.WebApp. Everything degrades to a no-op (or a
// plain-browser equivalent) outside Telegram, so screens never branch on it.
const W = () => window.Telegram?.WebApp

export const initData = W()?.initData || ''
export const isTg = !!initData
export const MODE = isTg ? 'tg' : 'browser'
export const tgUser = W()?.initDataUnsafe?.user || null

export function startTg() {
  document.documentElement.dataset.mode = MODE
  if (!isTg) return
  const w = W()
  const apply = () => { document.documentElement.dataset.scheme = w.colorScheme || 'light' }
  apply()
  try { w.onEvent?.('themeChanged', apply) } catch { /* old client */ }
  try { w.ready() } catch { /* ignore */ }
  try { w.expand() } catch { /* ignore */ }
  try { w.setHeaderColor?.('secondary_bg_color') } catch { /* old client */ }
  try { w.setBackgroundColor?.('secondary_bg_color') } catch { /* old client */ }
}

const safe = (fn) => { try { fn() } catch { /* unsupported on this client */ } }
export const haptic = {
  impact: (s = 'light') => isTg && safe(() => W().HapticFeedback.impactOccurred(s)),
  notify: (s = 'success') => isTg && safe(() => W().HapticFeedback.notificationOccurred(s)),
  select: () => isTg && safe(() => W().HapticFeedback.selectionChanged()),
}

export function confirmDialog(message) {
  return new Promise((resolve) => {
    if (isTg && W().showConfirm) {
      try { W().showConfirm(message, (ok) => resolve(!!ok)); return } catch { /* fall back */ }
    }
    resolve(window.confirm(message))
  })
}

export function openLink(url) {
  if (isTg) { try { W().openLink(url); return } catch { /* fall back */ } }
  window.open(url, '_blank', 'noopener')
}

// Save a file to the device. Telegram 8.0+ has a native downloader; older
// clients hand the URL to the system browser. Outside Telegram: a plain <a download>.
export function saveFile(url, fileName) {
  if (isTg) {
    const w = W()
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
  (isTg && W().readTextFromClipboard && W().isVersionAtLeast?.('6.4')) || navigator.clipboard?.readText || isTg)
export function readClipboard() {
  return new Promise((resolve) => {
    let pending = 0, done = false
    const finish = (txt) => {
      if (done) return
      if (txt && String(txt).trim()) { done = true; resolve(String(txt)); return }
      if (--pending <= 0) { done = true; resolve(null) }
    }
    if (navigator.clipboard?.readText) {
      pending++
      try { navigator.clipboard.readText().then(finish, () => finish(null)) } catch { finish(null) }
    }
    if (isTg && W().readTextFromClipboard && W().isVersionAtLeast?.('6.4')) {
      pending++
      try { W().readTextFromClipboard((txt) => finish(txt)) } catch { finish(null) }
    }
    if (!pending) { resolve(null); return }
    setTimeout(() => { if (!done) { done = true; resolve(null) } }, 1500)   // a callback that never comes
  })
}

export function openTelegramLink(url) {
  if (isTg) { try { W().openTelegramLink(url); return } catch { /* fall back */ } }
  window.open(url, '_blank', 'noopener')
}

// ---- BackButton: a stack of handlers; visible while the stack is non-empty.
const backStack = []
let backWired = false
function syncBack() {
  const bb = W()?.BackButton
  if (!bb) return
  if (backStack.length) bb.show(); else bb.hide()
}
export function pushBack(fn) {
  if (!isTg) return () => {}
  const bb = W().BackButton
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
export function mainButton({ text, enabled, loading, onClick }) {
  const mb = W().MainButton
  mb.setParams({ text, is_visible: true, is_active: !!enabled })
  if (loading) mb.showProgress(false); else mb.hideProgress()
  mb.onClick(onClick)
  return () => { mb.offClick(onClick); mb.hide(); mb.hideProgress() }
}
