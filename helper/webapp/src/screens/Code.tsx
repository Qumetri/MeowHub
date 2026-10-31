import { useState } from 'react'
import Icon from '../icons'
import { Action, Button, Cell, Section, toast } from '../ui'
import { api, inPreview } from '../api'
import { t } from '../i18n'
import { haptic, isTg, openInvoice, openTelegramLink } from '../tg'
import { fmtDate, normCode } from '../util'
import { useApp } from '../ctx'
import { ContactCell, Title, contactHandle, redeemMessage } from './shared'
import type { Member, PayCheck, PayInvoice, PayOffer, PayPlan, PayProvider, RedeemResponse } from '../types'

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

// Paid plans: Telegram Stars (Telegram's own payment sheet) and/or CryptoBot (opens @CryptoBot).
// The server credits the payment (successful_payment / the invoice poller); the app just refreshes.
function PayPlans({ offer, onPaid }: { offer: PayOffer; onPaid: (m: Member | null) => void }) {
  const [busy, setBusy] = useState('')

  async function pay(plan: PayPlan, provider: PayProvider) {
    if (busy) return
    if (inPreview()) { toast(t('pay.preview_off')); return }
    haptic.impact('light'); setBusy(plan.id + provider)
    try {
      const inv = await api<PayInvoice>('pay', { method: 'POST', body: { provider, plan: plan.id } })
      if (inv.provider === 'stars') {
        const status = await openInvoice(inv.link)
        if (status !== 'paid') {
          if (status !== 'cancelled') { haptic.notify('error'); toast(t('pay.failed'), 'bad') }
          return
        }
        haptic.notify('success'); toast(t('pay.paid'))
        // the bot credits on successful_payment, a moment after the sheet closes
        for (let i = 0; i < 6; i++) {
          await sleep(1500)
          const r = await api<PayCheck>('pay/check', { method: 'POST' })
          if (r.member) { onPaid(r.member); break }
        }
      } else {
        openTelegramLink(inv.url)
      }
    } catch (e) {
      haptic.notify('error'); toast((e as Error).message, 'bad')
    } finally { setBusy('') }
  }

  return (
    <Section title={t('pay.title')} footer={<>{offer.crypto ? t('pay.crypto_hint') + ' ' : ''}{offer.stars ? t('pay.stars_hint') + ' ' : ''}{t('pay.foot')}</>}>
      {offer.plans.map((p) => {
        const name = t('pay.plan.' + p.id)
        return (
          <Cell key={p.id} icon="calplus" title={name} sub={t('pay.days', { n: p.days })}
            right={
              <span className="paybtns">
                {offer.stars && (
                  <Button kind="primary" size="s" loading={busy === p.id + 'stars'} disabled={!!busy && busy !== p.id + 'stars'}
                    label={t('pay.stars_aria', { plan: name, n: p.stars })} onClick={() => { void pay(p, 'stars') }}>
                    ⭐ {p.stars}
                  </Button>
                )}
                {offer.crypto && (
                  <Button kind="tonal" size="s" loading={busy === p.id + 'crypto'} disabled={!!busy && busy !== p.id + 'crypto'}
                    label={t('pay.crypto_aria', { plan: name, n: p.usd })} onClick={() => { void pay(p, 'crypto') }}>
                    ${p.usd}
                  </Button>
                )}
              </span>
            } />
        )
      })}
    </Section>
  )
}

export default function Code() {
  const { me, refreshMe } = useApp()
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [ok, setOk] = useState<Member | null>(null)
  const ready = normCode(code).length >= 8
  const offer = isTg ? me.pay : null
  const sells = !!offer && (offer.stars || offer.crypto)

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

  const paid = (m: Member | null) => { if (m) setOk(m); refreshMe() }
  const h = contactHandle(me)

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
      {sells && <><p className="lead lead--left">{t('pay.lead')}</p><PayPlans offer={offer} onPaid={paid} /></>}
      {sells && <h3 className="form__h">{t('code.section')}</h3>}
      <p className="lead lead--left">{t('code.lead')}</p>
      <div className="codebox">
        <input className={'codeinput' + (err ? ' bad' : '')} value={code} placeholder="MEOW-XXXX-XXXX" autoFocus={!ok && !sells}
          autoCapitalize="characters" autoCorrect="off" autoComplete="off" spellCheck={false} enterKeyHint="go"
          aria-label={t('stranger.code')}
          onChange={(e) => { setCode(e.target.value.toUpperCase()); setErr(''); setOk(null) }}
          onKeyDown={(e) => { if (e.key === 'Enter') void submit() }} />
        {err && <p className="codeerr" role="alert">{err}</p>}
      </div>
      <Action text={t('code.submit')} enabled={ready} loading={busy} onClick={submit} />
      <Section>
        {h
          ? <Cell icon="send" title={t('pay.direct', { contact: h })} chevron
              onClick={() => { haptic.impact('light'); openTelegramLink('https://t.me/' + h.slice(1)) }} />
          : <ContactCell me={me} />}
      </Section>
    </div>
  )
}
