import { useCallback, useMemo, useRef, useState } from 'react'
import Icon from './icons.jsx'
import { AppCtx } from './ctx.js'
import { MODE, haptic, isTg, openTelegramLink } from './tg.js'
import { useBack } from './hooks.js'
import Stranger from './screens/Stranger.jsx'
import Home from './screens/Home.jsx'
import Pushed from './Pushed.jsx'

export const PREVIEW_KINDS = [
  { id: 'guest', role: 'stranger', label: 'Гость', sub: 'Видит экран ввода кода', icon: 'user' },
  { id: 'member', role: 'member', label: 'Участник', sub: 'Активная подписка, VPN и мессенджер', icon: 'shield' },
  { id: 'expired', role: 'expired', label: 'Истёк', sub: 'Срок вышел, сервисы на паузе', icon: 'clock' },
]
export const kindLabel = (id) => PREVIEW_KINDS.find((k) => k.id === id)?.label || id

// The real member screens fed by mock data (api() is switched to the mock by App.jsx).
// Own navigation stack so the owner's admin stack stays untouched underneath.
export default function PreviewShell({ me, onRefresh }) {
  const [stack, setStack] = useState([])
  const scrolls = useRef([])
  const go = useCallback((screen, params, replace) => {
    if (screen === 'tab') return
    if (replace) { setStack((s) => [...s.slice(0, -1), { s: screen, p: params }]); return }
    scrolls.current.push(window.scrollY)
    setStack((s) => [...s, { s: screen, p: params }])
    window.scrollTo(0, 0)
  }, [])
  const back = useCallback(() => {
    setStack((s) => s.slice(0, -1))
    const y = scrolls.current.pop() || 0
    requestAnimationFrame(() => window.scrollTo(0, y))
  }, [])
  useBack(back, stack.length > 0)

  const ctx = useMemo(() => ({
    me, go, back, depth: stack.length, mode: MODE, version: 0, bump: () => {},
    refreshMe: onRefresh, adminFilter: 'all', setAdminFilter: () => {},
  }), [me, go, back, stack.length, onRefresh])

  return (
    <AppCtx.Provider value={ctx}>
      {me.role === 'stranger' ? <Stranger /> : (
        <>
          <div hidden={stack.length > 0}><Home /></div>
          {stack.length > 0 && <Pushed entry={stack[stack.length - 1]} />}
        </>
      )}
    </AppCtx.Provider>
  )
}

export function PreviewBanner({ kind, botUsername, onExit }) {
  const url = botUsername ? 'https://t.me/' + botUsername : null
  return (
    <div className="pvbar" role="status">
      <span className="pvbar__t"><Icon name="eye" size={16} /><b>Предпросмотр</b> · {kindLabel(kind)}</span>
      <span className="pvbar__a">
        {url && (isTg
          ? <button type="button" className="pvbar__b" onClick={() => { haptic.impact('light'); openTelegramLink(url) }}>Открыть @{botUsername}</button>
          : <a className="pvbar__b" href={url} target="_blank" rel="noopener noreferrer">Открыть @{botUsername}</a>)}
        <button type="button" className="pvbar__b pvbar__b--x" onClick={() => { haptic.impact('light'); onExit() }}>Выйти</button>
      </span>
    </div>
  )
}
