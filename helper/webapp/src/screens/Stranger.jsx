import { useEffect, useState } from 'react'
import { Paw } from '../icons.jsx'
import { Action, Section } from '../ui.jsx'
import { api } from '../api.js'
import { t } from '../i18n.js'
import { haptic } from '../tg.js'
import { normCode, plural } from '../util.js'
import { useApp } from '../ctx.js'
import { ContactCell, LangSection, redeemMessage } from './shared.jsx'
import Icon from '../icons.jsx'

export default function Stranger() {
  const { me, refreshMe } = useApp()
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [done, setDone] = useState(false)
  const ready = normCode(code).length >= 8
  // A newcomer may start the free trial instead: the main button offers it until a code is typed.
  const trial = me?.trial?.state === 'ok' && !normCode(code)
  const period = me?.trial ? `${me.trial.days} ${plural(me.trial.days, t('stranger.day_forms').split('|'))}` : ''

  useEffect(() => {
    if (!done) return undefined
    const id = setTimeout(() => refreshMe(), 1300)
    return () => clearTimeout(id)
  }, [done, refreshMe])

  async function startTrial() {
    if (busy) return
    haptic.impact('light')
    setBusy(true); setErr('')
    try {
      const r = await api('trial', { method: 'POST' })
      if (r.result === 'new') { haptic.notify('success'); setDone('trial') } else {
        haptic.notify('error'); setErr(r.message || t('stranger.trial_fail'))
      }
    } catch (e) {
      haptic.notify('error'); setErr(e.message)
    } finally { setBusy(false) }
  }

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
        <h1 className="hero">{t(done === 'trial' ? 'stranger.trial_done' : 'stranger.success')}</h1>
        <p className="lead">{t('stranger.success_sub')}</p>
      </div>
    )
  }

  return (
    <div className="page page--center">
      <div className="brandmark"><Paw size={34} /></div>
      <h1 className="hero">MeowHub</h1>
      {me?.trial?.state === 'ok' ? (
        <>
          <p className="lead">{t('stranger.trial_lead', { period })}</p>
          <p className="lead trial-after">{t('stranger.trial_after', { contact: me.contact || t('contact.owner') })}</p>
          <p className="trial-or">{t('stranger.or_code')}</p>
        </>
      ) : <p className="lead">{t('stranger.lead')}</p>}
      <div className="codebox">
        <input className={'codeinput' + (err ? ' bad' : '')} value={code} autoFocus
          placeholder="MEOW-XXXX-XXXX" aria-label={t('stranger.code')}
          autoCapitalize="characters" autoCorrect="off" autoComplete="off" spellCheck={false} enterKeyHint="go"
          onChange={(e) => { setCode(e.target.value.toUpperCase()); setErr('') }}
          onKeyDown={(e) => { if (e.key === 'Enter') submit() }} />
        {err && <p className="codeerr" role="alert">{err}</p>}
      </div>
      {trial
        ? <Action text={t('stranger.trial_btn', { period })} loading={busy} onClick={startTrial} />
        : <Action text={t('stranger.activate')} enabled={ready} loading={busy} onClick={submit} />}
      <div className="wide-col">
        <Section><ContactCell me={me} /></Section>
        <LangSection />
      </div>
    </div>
  )
}
