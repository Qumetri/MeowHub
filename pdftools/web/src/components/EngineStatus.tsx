import { useEffect, useState } from 'react'
import { T } from '../lib/i18n'

// One cheap ping so a stopped engine shows up before someone uploads a 100 MB scan.
export default function EngineStatus() {
  const [state, setState] = useState<'checking' | 'up' | 'down'>('checking')
  useEffect(() => {
    const ctl = new AbortController()
    fetch('api/v1/info/status', { signal: ctl.signal, cache: 'no-store' })
      .then((r) => setState(r.ok ? 'up' : 'down'))
      .catch((e: Error) => { if (e.name !== 'AbortError') setState('down') })
    return () => ctl.abort()
  }, [])
  const text = {
    checking: T('Проверяю движок…', 'Checking the engine…'),
    up: T('Движок на связи', 'Engine is online'),
    down: T('Движок не отвечает — серверные инструменты сейчас не сработают', 'The engine is not answering: server tools won’t work right now'),
  }[state]
  return <p className={`engine engine-${state}`} role="status"><span className="dot" aria-hidden="true" />{text}</p>
}
