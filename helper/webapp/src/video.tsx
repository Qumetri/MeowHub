// The standalone video page (video.html at its own secret path, VIDEO_PATH): the Mini App's
// downloader without Telegram and without a login. No telegram-web-app.js here; the API is
// /api/pub/* with an anonymous signed id (guest.ts), and the owner switches it in /services.
import './styles.css'
import React, { useState } from 'react'
import { createRoot } from 'react-dom/client'
import { Paw } from './icons'
import { Toaster } from './ui'
import { setGuestAuth } from './api'
import { guestAuth } from './guest'
import { cacheLang, cachedLang, resolveLang, setLang, t } from './i18n'
import { Downloader } from './screens/Downloads'
import type { Lang } from './types'

document.documentElement.dataset.mode = 'browser'
setGuestAuth(guestAuth)
const LANGS: Lang[] = ['ru', 'en']

function VideoPage() {
  const [lang, pick] = useState<Lang>(() => setLang(cachedLang() || resolveLang(navigator.language)))
  document.title = t('vp.doc_title')

  function choose(l: Lang) {
    setLang(l); cacheLang(l); pick(l)
  }

  return (
    <div className="app app--browser">
      <header className="topbar">
        <div className="topbar__in">
          <span className="topbar__brand"><span className="topbar__logo"><Paw size={18} /></span>MeowHub<em> · {t('vp.brand')}</em></span>
          <div className="toptabs" role="group" aria-label={t('vp.lang')}>
            {LANGS.map((l) => (
              <button key={l} type="button" className={l === lang ? 'on' : ''} aria-pressed={l === lang}
                onClick={() => choose(l)}>{l.toUpperCase()}</button>
            ))}
          </div>
        </div>
      </header>
      <main>
        {/* keyed by language: presets and messages come from the server in that language */}
        <Downloader key={lang} base="pub/dl" mine={t('vp.mine')}
          head={<header className="title"><h1>{t('vp.title')}</h1><p>{t('vp.sub')}</p></header>} />
      </main>
      <Toaster />
    </div>
  )
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><VideoPage /></React.StrictMode>)
