import React from 'react'
import { createRoot } from 'react-dom/client'
import { cachedLang, resolveLang, setLang, t } from './i18n.js'
import { isTg, startTg, tgUser } from './tg.js'
import App from './App.jsx'

// Best guess until /api/me delivers the real (server-side) preference.
setLang(cachedLang() || (isTg ? resolveLang(tgUser?.language_code) : 'ru'))
startTg()
class Crash extends React.Component {
  constructor(p) { super(p); this.state = { err: null } }
  static getDerivedStateFromError(err) { return { err } }
  componentDidCatch(err, info) { console.error('render crash', err, info?.componentStack) }
  render() {
    if (!this.state.err) return this.props.children
    return (
      <div className="crash" role="alert">
        <h2>{t('crash.title')}</h2>
        <p>{t('crash.text')}</p>
        <button type="button" className="btn btn--primary" onClick={() => location.reload()}>{t('crash.restart')}</button>
        <pre>{String(this.state.err?.message || this.state.err).slice(0, 300)}</pre>
      </div>
    )
  }
}

createRoot(document.getElementById('root')).render(<React.StrictMode><Crash><App /></Crash></React.StrictMode>)
