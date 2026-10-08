import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import Icon, { Paw } from './icons.jsx'
import { AppCtx } from './ctx.js'
import { api, setPreviewApi } from './api.js'
import { cacheLang, getLang, setLang, resolveLang, t } from './i18n.js'
import { MODE, haptic, isTg, tgUser } from './tg.js'
import { useBack } from './hooks.js'
import { Cell, ErrorBox, Sheet, Skeleton, Toaster, toast } from './ui.jsx'
import Stranger from './screens/Stranger.jsx'
import Home from './screens/Home.jsx'
import Pushed from './Pushed.jsx'
import PreviewShell, { PREVIEW_KINDS, PreviewBanner } from './Preview.jsx'
import Members from './admin/Members.jsx'
import Codes from './admin/Codes.jsx'
import Overview from './admin/Overview.jsx'

const PAGES = ['vpn', 'matrix', 'admin', 'code', 'downloads']
const TAB_META = {
  members: { icon: 'users', label: 'nav.members' },
  codes: { icon: 'ticket', label: 'nav.codes' },
  overview: { icon: 'chart', label: 'nav.overview' },
  me: { icon: 'user', label: 'nav.me' },
}

function Tabs({ tabs, tab, onPick, className }) {
  return (
    <nav className={className} aria-label={t('nav.sections')}>
      {tabs.map((id) => (
        <button key={id} type="button" className={tab === id ? 'on' : ''} aria-current={tab === id ? 'page' : undefined}
          onClick={() => { haptic.select(); onPick(id) }}>
          <Icon name={TAB_META[id].icon} size={isTg ? 24 : 18} />
          <span>{t(TAB_META[id].label)}</span>
        </button>
      ))}
    </nav>
  )
}

export default function App() {
  const [meSt, setMeSt] = useState({ loading: true })
  const [stack, setStack] = useState([])
  const [tab, setTab] = useState(null)
  const [visited, setVisited] = useState(() => new Set())
  const [version, setVersion] = useState(0)
  const [adminFilter, setAdminFilter] = useState('all')
  const scrolls = useRef([])
  const booted = useRef(false)
  const [preview, setPreview] = useState(null) // { kind, me } while the owner previews the member UI
  const [pickOpen, setPickOpen] = useState(false)
  const [pvBusy, setPvBusy] = useState(null)
  const mockMod = useRef(null)
  // UI language + the user's stored preference ('auto' | 'ru' | 'en'). `lang` in state (and in
  // the context value) is what re-renders the whole tree when it changes; t() reads the module value.
  const [lang, setLangState] = useState(getLang())
  const [langPref, setLangPref] = useState('auto')
  const langPrefRef = useRef('auto')
  langPrefRef.current = langPref
  const realLang = useRef(null) // the owner's real language while a preview runs on mock data
  const applyLang = useCallback((code, pref) => {
    const l = setLang(code)
    setLangState(l)
    if (pref) setLangPref(pref)
    return l
  }, [])

  const loadMe = useCallback(async (silent) => {
    if (!silent) setMeSt({ loading: true })
    try {
      const me = await api('me')
      if (me.lang) { applyLang(me.lang, me.lang_pref); cacheLang(getLang()) }
      setMeSt({ me })
    } catch (error) {
      setMeSt((s) => (silent && s.me ? s : { error }))
    }
  }, [applyLang])
  useEffect(() => { loadMe() }, [loadMe])

  const me = meSt.me
  const owner = me?.role === 'owner'
  const canPreview = !!me?.can_preview && owner
  const tabs = useMemo(() => (owner ? ['members', 'codes', 'overview', ...(me.member ? ['me'] : [])] : []), [owner, me])

  // Initial page from ?p=, applied once when /api/me arrives.
  useEffect(() => {
    if (!me || booted.current) return
    booted.current = true
    const p = new URLSearchParams(location.search).get('p')
    const first = owner ? (MODE === 'browser' ? 'overview' : 'members') : null
    const entries = []
    let startTab = first
    if (me.role !== 'stranger' && PAGES.includes(p) && p !== 'admin') {
      if (owner && me.member) startTab = 'me'
      entries.push({ s: p === 'code' ? 'code' : p, p: null })
    }
    if (owner && p === 'admin') startTab = MODE === 'browser' ? 'overview' : 'members'
    // Dev only: ?tab=overview opens that admin tab (for screenshots).
    if (import.meta.env.DEV && owner) { const q = new URLSearchParams(location.search).get('tab'); if (q && TAB_META[q]) startTab = q }
    setTab(startTab)
    if (startTab) setVisited(new Set([startTab]))
    setStack(entries)
  }, [me, owner])

  const go = useCallback((screen, params, replace) => {
    if (screen === 'tab') {
      setTab(params); setVisited((v) => new Set(v).add(params)); setStack([]); scrolls.current = []
      window.scrollTo(0, 0)
      return
    }
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
  useBack(back, stack.length > 0 && !preview)

  useLayoutEffect(() => {
    if (MODE === 'browser') document.title = owner ? `MeowHub · ${t('app.bots')}` : 'MeowHub'
  }, [owner, lang])

  // ---- owner preview: mock.js is imported only here, so it stays a separate lazy chunk.
  const startPreview = useCallback(async (kind) => {
    const def = PREVIEW_KINDS.find((k) => k.id === kind)
    if (!def) return
    setPvBusy(kind)
    try {
      const m = mockMod.current || (mockMod.current = await import('./mock.js'))
      if (!realLang.current) realLang.current = { lang: getLang(), pref: langPrefRef.current }
      m.configure(def.role, { lang: getLang(), pref: langPrefRef.current })
      setPreviewApi(m.handle)
      const pme = await m.handle('GET', 'me')
      setPreview({ kind, me: pme })
      setPickOpen(false)
      window.scrollTo(0, 0)
    } catch (e) {
      setPreviewApi(null)
      toast(e?.message || t('err.generic'), 'bad')
    } finally { setPvBusy(null) }
  }, [])
  const exitPreview = useCallback(() => {
    setPreviewApi(null)
    mockMod.current?.configure(null)
    if (realLang.current) { applyLang(realLang.current.lang, realLang.current.pref); realLang.current = null }
    setPreview(null)
    window.scrollTo(0, 0)
  }, [applyLang])
  // A guest who redeems a code becomes a member, like the real app after /api/me reloads.
  const refreshPreview = useCallback(() => {
    if (preview) startPreview(preview.kind === 'guest' ? 'member' : preview.kind)
  }, [preview, startPreview])

  // Language switch: optimistic local change (the whole tree re-renders), then POST /api/lang.
  // In preview the mock handles it, so nothing reaches the server.
  const changeLang = useCallback(async (pref) => {
    if (pref === langPref) return
    const prev = { lang: getLang(), pref: langPref }
    const guess = pref === 'auto' ? resolveLang(meSt.me?.user?.language_code ?? tgUser?.language_code) : pref
    haptic.select()
    applyLang(guess, pref)
    try {
      const r = await api('lang', { method: 'POST', body: { lang: pref } })
      applyLang(r.lang, r.lang_pref)
      if (!preview) cacheLang(r.lang)
      setVersion((v) => v + 1) // admin lists re-fetch (server-side texts change language)
      if (preview) {
        const pme = await mockMod.current.handle('GET', 'me')
        setPreview((p) => (p ? { ...p, me: pme } : p))
      } else loadMe(true)
    } catch (e) {
      applyLang(prev.lang, prev.pref)
      haptic.notify('error'); toast(e?.message || t('err.generic'), 'bad')
    }
  }, [langPref, meSt.me, preview, applyLang, loadMe])

  // Dev only: ?preview=guest|member|expired starts a preview straight away.
  useEffect(() => {
    if (!import.meta.env.DEV || !canPreview) return
    const k = new URLSearchParams(location.search).get('preview')
    if (k) startPreview(k)
  }, [canPreview, startPreview])

  const ctx = useMemo(() => ({
    me, go, back, depth: stack.length, mode: MODE, version, bump: () => setVersion((v) => v + 1),
    refreshMe: () => loadMe(true), adminFilter, setAdminFilter,
    openPreview: canPreview ? () => setPickOpen(true) : null,
    lang, langPref, setLangPref: changeLang,
  }), [me, go, back, stack.length, version, loadMe, adminFilter, canPreview, lang, langPref, changeLang])

  let body
  if (preview) {
    body = <PreviewShell key={preview.kind} me={preview.me} onRefresh={refreshPreview} lang={lang} langPref={langPref} setLangPref={changeLang} />
  } else if (meSt.error) {
    body = <div className="page"><ErrorBox error={meSt.error} onRetry={() => loadMe()} what={t('err.load')} /></div>
  } else if (!me) {
    body = <div className="page"><Skeleton rows={3} title={false} /><Skeleton rows={2} /></div>
  } else if (me.role === 'stranger') {
    body = <Stranger />
  } else if (owner) {
    const deep = stack.length > 0
    body = (
      <>
        <div hidden={deep}>
          {tabs.filter((id) => visited.has(id)).map((id) => (
            <div key={id} hidden={tab !== id}>
              {id === 'members' && <Members />}
              {id === 'codes' && <Codes />}
              {id === 'overview' && <Overview />}
              {id === 'me' && <Home />}
            </div>
          ))}
        </div>
        {deep && <Pushed entry={stack[stack.length - 1]} />}
      </>
    )
  } else {
    body = (
      <>
        <div hidden={stack.length > 0}><Home /></div>
        {stack.length > 0 && <Pushed entry={stack[stack.length - 1]} />}
      </>
    )
  }

  const showTabs = owner && tabs.length > 0 && !preview
  const pickTab = (id) => go('tab', id)

  return (
    <AppCtx.Provider value={ctx}>
      <div className={`app app--${MODE}${showTabs && isTg && stack.length === 0 ? ' app--tabs' : ''}${owner ? ' app--owner' : ''}`}>
        {!isTg && (
          <header className="topbar">
            <div className="topbar__in">
              <span className="topbar__brand"><span className="topbar__logo"><Paw size={18} /></span>MeowHub{owner && !preview && <em> · {t('app.bots')}</em>}</span>
              {showTabs && <Tabs tabs={tabs} tab={tab} onPick={pickTab} className="toptabs" />}
              {canPreview && !preview && (
                <button type="button" className="pvbtn" onClick={() => { haptic.impact('light'); setPickOpen(true) }}>
                  <Icon name="eye" size={16} /><span>{t('pv.view_as')}</span>
                </button>
              )}
            </div>
            {preview && <PreviewBanner kind={preview.kind} botUsername={me?.member_bot_username} onExit={exitPreview} />}
          </header>
        )}
        {isTg && preview && <PreviewBanner kind={preview.kind} botUsername={me?.member_bot_username} onExit={exitPreview} />}
        <main>{body}</main>
        {isTg && showTabs && stack.length === 0 && <Tabs tabs={tabs} tab={tab} onPick={pickTab} className="tabbar" />}
        <Sheet open={pickOpen} title={t('pv.view_as')} onClose={() => setPickOpen(false)}>
          <div className="group">
            {PREVIEW_KINDS.map((k) => (
              <Cell key={k.id} icon={k.icon} title={t('pv.' + k.id)} sub={t('pv.' + k.id + '_sub')} chevron={pvBusy !== k.id} disabled={!!pvBusy && pvBusy !== k.id}
                right={pvBusy === k.id ? <span className="spin" /> : undefined}
                onClick={() => { haptic.impact('light'); startPreview(k.id) }} />
            ))}
          </div>
          <p className="sec__f">{t('pv.note')}{me?.member_bot_username ? t('pv.note_real', { bot: me.member_bot_username }) : ''}</p>
        </Sheet>
        <Toaster />
      </div>
    </AppCtx.Provider>
  )
}
