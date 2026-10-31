import { useEffect, useState } from 'react'

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
  const text = { checking: 'Проверяю движок…', up: 'Движок на связи', down: 'Движок не отвечает — серверные инструменты сейчас не сработают' }[state]
  return <p className={`engine engine-${state}`} role="status"><span className="dot" aria-hidden="true" />{text}</p>
}
