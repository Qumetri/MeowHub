import { useCallback, useMemo, useRef, useState } from 'react'
import Icon from './icons'
import { t } from './i18n'
import { AppCtx } from './ctx'
import { MODE, haptic, isTg, openTelegramLink } from './tg'
import { useBack } from './hooks'
import Stranger from './screens/Stranger'
import Home from './screens/Home'
import Pushed from './Pushed'
import type { IconName } from './icons'
import type { AppContext, Lang, LangPref, Me, StackEntry } from './types'

export type PreviewKind = 'guest' | 'member' | 'expired'
export const PREVIEW_KINDS: { id: PreviewKind; role: 'stranger' | 'member' | 'expired'; icon: IconName }[] = [
  { id: 'guest', role: 'stranger', icon: 'user' },
  { id: 'member', role: 'member', icon: 'shield' },
  { id: 'expired', role: 'expired', icon: 'clock' },
]
// Labels live in the dictionary: pv.<id> and pv.<id>_sub.
export const kindLabel = (id: string): string => (PREVIEW_KINDS.some((k) => k.id === id) ? t('pv.' + id) : id)

// The real member screens fed by mock data (api() is switched to the mock by App.jsx).
// Own navigation stack so the owner's admin stack stays untouched underneath.
interface ShellProps { me: Me; onRefresh: () => void; lang: Lang; langPref: LangPref; setLangPref: (p: LangPref) => void }

export default function PreviewShell({ me, onRefresh, lang, langPref, setLangPref }: ShellProps) {
  const [stack, setStack] = useState<StackEntry[]>([])
  const scrolls = useRef<number[]>([])
  const go = useCallback<AppContext['go']>((screen: string, params?: unknown, replace?: boolean) => {
    if (screen === 'tab') return
    const entry = { s: screen, p: params ?? null } as StackEntry
    if (replace) { setStack((s) => [...s.slice(0, -1), entry]); return }
    scrolls.current.push(window.scrollY)
    setStack((s) => [...s, entry])
    window.scrollTo(0, 0)
  }, [])
  const back = useCallback(() => {
    setStack((s) => s.slice(0, -1))
    const y = scrolls.current.pop() || 0
    requestAnimationFrame(() => window.scrollTo(0, y))
  }, [])
  useBack(back, stack.length > 0)

  const ctx = useMemo<AppContext>(() => ({
    me, go, back, depth: stack.length, mode: MODE, version: 0, bump: () => {},
    refreshMe: onRefresh, adminFilter: 'all', setAdminFilter: () => {},
    lang, langPref, setLangPref,
  }), [me, go, back, stack.length, onRefresh, lang, langPref, setLangPref])

  return (
    <AppCtx.Provider value={ctx}>
      {me.role === 'stranger' ? <Stranger /> : (
        <>
          <div hidden={stack.length > 0}><Home /></div>
          {stack.length > 0 && <Pushed entry={stack[stack.length - 1]!} />}
        </>
      )}
    </AppCtx.Provider>
  )
}

export function PreviewBanner({ kind, botUsername, onExit }: { kind: string; botUsername?: string; onExit: () => void }) {
  const url = botUsername ? 'https://t.me/' + botUsername : null
  return (
    <div className="pvbar" role="status">
      <span className="pvbar__t"><Icon name="eye" size={16} /><b>{t('pv.preview')}</b> · {kindLabel(kind)}</span>
      <span className="pvbar__a">
        {url && (isTg
          ? <button type="button" className="pvbar__b" onClick={() => { haptic.impact('light'); openTelegramLink(url) }}>{t('pv.open_bot', { bot: botUsername })}</button>
          : <a className="pvbar__b" href={url} target="_blank" rel="noopener noreferrer">{t('pv.open_bot', { bot: botUsername })}</a>)}
        <button type="button" className="pvbar__b pvbar__b--x" onClick={() => { haptic.impact('light'); onExit() }}>{t('pv.exit')}</button>
      </span>
    </div>
  )
}
