import { useState } from 'react'
import Icon from '../icons'
import { Action, Section } from '../ui'
import { api } from '../api'
import { t } from '../i18n'
import { haptic } from '../tg'
import { fmtDate, normCode } from '../util'
import { useApp } from '../ctx'
import { ContactCell, Title, redeemMessage } from './shared'
import type { Member, RedeemResponse } from '../types'

export default function Code() {
  const { me, refreshMe } = useApp()
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ok, setOk] = useState<Member | null>(null)
  const ready = normCode(code).length >= 8

  async function submit() {
    if (!ready || busy) return
    haptic.impact('light')
    setBusy(true); setErr('')
    try {
      const r = await api<RedeemResponse>('redeem', { method: 'POST', body: { code: normCode(code) } })
      if (r.result === 'new' || r.result === 'extended') {
        haptic.notify('success'); setOk(r.member); setCode('')
        refreshMe()
      } else { haptic.notify('error'); setErr(redeemMessage(r.result, me, r.message)) }
    } catch (e) { haptic.notify('error'); setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <div className="page">
      <Title>{t('code.title')}</Title>
      {ok && (
        <div className="okbox" role="status">
          <span className="okbox__ic"><Icon name="check" size={20} strokeWidth={2.6} /></span>
          <div>
            <b>{ok.expires_ts ? t('redeem.extended', { date: fmtDate(ok.expires_ts, true) }) : t('redeem.new')}</b>
          </div>
        </div>
      )}
      <p className="lead lead--left">{t('code.lead')}</p>
      <div className="codebox">
        <input className={'codeinput' + (err ? ' bad' : '')} value={code} placeholder="MEOW-XXXX-XXXX" autoFocus={!ok}
          autoCapitalize="characters" autoCorrect="off" autoComplete="off" spellCheck={false} enterKeyHint="go"
          aria-label={t('stranger.code')}
          onChange={(e) => { setCode(e.target.value.toUpperCase()); setErr(''); setOk(null) }}
          onKeyDown={(e) => { if (e.key === 'Enter') void submit() }} />
        {err && <p className="codeerr" role="alert">{err}</p>}
      </div>
      <Action text={t('code.submit')} enabled={ready} loading={busy} onClick={submit} />
      <Section><ContactCell me={me} /></Section>
    </div>
  )
}
