// Two languages. The page starts in the browser's language (the first of ru/en in its list,
// English when neither is there); ?lang=en|ru or the RU/EN switch overrides that, and the switch
// is remembered per browser. Texts sit next to their use as T('по-русски', 'in English') or, in
// data that is built once (tools.ts), as L(ru, en) read later with tx() — so the compiler makes
// sure neither language is missing.
import { useSyncExternalStore } from 'react'

export type Lang = 'ru' | 'en'
/** a text in both languages, or one that reads the same in both (DOCX, 90°) */
export type Text = string | { ru: string; en: string }

const KEY = 'pdftools-lang'
const isLang = (s: string | null | undefined): s is Lang => s === 'ru' || s === 'en'

function initial(): Lang {
  const asked = new URLSearchParams(window.location.search).get('lang')
  if (isLang(asked)) return asked
  try {
    const saved = localStorage.getItem(KEY)
    if (isLang(saved)) return saved
  } catch { /* storage blocked: fall back to the browser */ }
  for (const tag of navigator.languages?.length ? navigator.languages : [navigator.language]) {
    const base = tag?.slice(0, 2).toLowerCase()
    if (isLang(base)) return base
  }
  return 'en'
}

let lang: Lang = initial()
const subs = new Set<() => void>()
document.documentElement.lang = lang

export const getLang = (): Lang => lang

// Saved even when it is already shown: the page may be showing it only because of ?lang=.
export function setLang(next: Lang): void {
  try { localStorage.setItem(KEY, next) } catch { /* private mode: this visit only */ }
  // a ?lang= in the address would win again on reload
  const url = new URL(window.location.href)
  if (url.searchParams.has('lang')) {
    url.searchParams.delete('lang')
    history.replaceState(null, '', url)
  }
  if (next === lang) return
  lang = next
  document.documentElement.lang = next
  subs.forEach((f) => f())
}

const subscribe = (f: () => void) => { subs.add(f); return () => { subs.delete(f) } }
/** re-renders the caller when the language changes */
export const useLang = (): Lang => useSyncExternalStore(subscribe, getLang)

export const T = (ru: string, en: string): string => (lang === 'en' ? en : ru)
export const L = (ru: string, en: string): Text => ({ ru, en })
export const tx = (t: Text): string => (typeof t === 'string' ? t : t[lang])
export const locale = (): string => (lang === 'en' ? 'en-GB' : 'ru-RU')

export const APP_NAME = L('Канцелярия', 'Paperwork')
