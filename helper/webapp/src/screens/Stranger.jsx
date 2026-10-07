import { useEffect, useState } from 'react'
import { Paw } from '../icons.jsx'
import { Action, Section } from '../ui.jsx'
import { api } from '../api.js'
import { t } from '../i18n.js'
import { haptic } from '../tg.js'
import { normCode } from '../util.js'
import { useApp } from '../ctx.js'
import { ContactCell, contactHandle, redeemMessage } from './shared.jsx'
import Icon from '../icons.jsx'

export default function Stranger() {
  const { me, refreshMe } = useApp()
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(false)
  const ready = normCode(code).length >= 8

  useEffect(() => {
    if (!done) return undefined
    const id = setTimeout(() => refreshMe(), 1300)
    return () => clearTimeout(id)
  }, [done, refreshMe])

  async function submit() {
    if (!ready || busy) return
    haptic.impact('light')
    setBusy(true); setErr('')
    try {
      const r = await api('redeem', { method: 'POST', body: { code: normCode(code) } })
      if (r.result === 'new' || r.result === 'extended') { haptic.notify('success'); setDone(true) } else {
        haptic.notify('error'); setErr(redeemMessage(r.result, me, r.message))
      }
    } catch (e) {
      haptic.notify('error')
      setErr(e.code === 'rate_limited' ? redeemMessage('rate_limited', me) : e.message)
    } finally { setBusy(false) }
  }

  if (done) {
    return (
      <div className="page page--center">
        <div className="brandmark ok"><Icon name="check" size={34} strokeWidth={2.6} /></div>
        <h1 className="hero">{t('stranger.success')}</h1>
        <p className="lead">{t('stranger.success_sub')}</p>
      </div>
    )
  }

  return (
    <div className="page page--center">
      <div className="brandmark"><Paw size={34} /></div>
      <h1 className="hero">MeowHub</h1>
      <p className="lead">{t('stranger.lead')}</p>
      <div className="codebox">
        <input className={'codeinput' + (err ? ' bad' : '')} value={code} autoFocus
          placeholder="MEOW-XXXX-XXXX" aria-label={t('stranger.code')}
          autoCapitalize="characters" autoCorrect="off" autoComplete="off" spellCheck={false} enterKeyHint="go"
          onChange={(e) => { setCode(e.target.value.toUpperCase()); setErr('') }}
          onKeyDown={(e) => { if (e.key === 'Enter') submit() }} />
        {err && <p className="codeerr" role="alert">{err}</p>}
      </div>
      <Action text={t('stranger.activate')} enabled={ready} loading={busy} onClick={submit} />
      <div className="wide-col">
        {contactHandle(me) && <Section><ContactCell me={me} /></Section>}
      </div>
    </div>
  )
}
