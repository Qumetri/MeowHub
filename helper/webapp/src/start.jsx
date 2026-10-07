import React from 'react'
import { createRoot } from 'react-dom/client'
import { setLang } from './i18n.js'
import { isTg, startTg, tgUser } from './tg.js'
import App from './App.jsx'

setLang(isTg ? tgUser?.language_code : 'ru')
startTg()
class Crash extends React.Component {
  constructor(p) { super(p); this.state = { err: null } }
  static getDerivedStateFromError(err) { return { err } }
  componentDidCatch(err, info) { console.error('render crash', err, info?.componentStack) }
  render() {
    if (!this.state.err) return this.props.children
    return (
      <div className="crash" role="alert">
        <h2>Что-то пошло не так</h2>
        <p>Экран не смог открыться. Попробуй ещё раз — если повторится, напиши владельцу.</p>
        <button type="button" className="btn btn--primary" onClick={() => location.reload()}>Перезапустить</button>
        <pre>{String(this.state.err?.message || this.state.err).slice(0, 300)}</pre>
      </div>
    )
  }
}

createRoot(document.getElementById('root')).render(<React.StrictMode><Crash><App /></Crash></React.StrictMode>)
