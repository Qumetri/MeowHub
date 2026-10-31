import React, { type ErrorInfo, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import { cachedLang, resolveLang, setLang, t } from './i18n'
import { isTg, startTg, tgUser } from './tg'
import App from './App'

// Best guess until /api/me delivers the real (server-side) preference.
setLang(cachedLang() || (isTg ? resolveLang(tgUser?.language_code) : 'ru'))
startTg()
class Crash extends React.Component<{ children: ReactNode }, { err: unknown }> {
  state = { err: null as unknown }
  static getDerivedStateFromError(err: unknown) { return { err } }
  componentDidCatch(err: unknown, info: ErrorInfo) { console.error('render crash', err, info?.componentStack) }
  render() {
    if (!this.state.err) return this.props.children
    return (
      <div className="crash" role="alert">
        <h2>{t('crash.title')}</h2>
        <p>{t('crash.text')}</p>
        <button type="button" className="btn btn--primary" onClick={() => location.reload()}>{t('crash.restart')}</button>
        <pre>{String(this.state.err instanceof Error ? this.state.err.message : this.state.err).slice(0, 300)}</pre>
      </div>
    )
  }
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><Crash><App /></Crash></React.StrictMode>)
