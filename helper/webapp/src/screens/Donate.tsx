import { useState } from 'react'
import { Button, Cell, Chips, Section, toast } from '../ui'
import { api, inPreview } from '../api'
import { t } from '../i18n'
import { haptic, openInvoice, openLink, openTelegramLink } from '../tg'
import type { PayInvoice, PayOffer } from '../types'

const CUSTOM = 'custom'
type Method = 'stars' | 'crypto'

/** Is there any way to donate in this offer? (Home and Help show the block only then.) */
export function canDonate(offer: PayOffer | null | undefined): offer is PayOffer {
  return !!offer && (!!offer.donate?.length || !!offer.donate_usd?.length || !!offer.donate_link)
}

// "Support the project": Telegram Stars, a CryptoBot invoice in USD, and/or the owner's external page
// (DONATE_LINK, e.g. Buy Me a Coffee). Shown inside Telegram while the owner's donate switch is on;
// it never touches the member's term.
export default function Donate({ offer, compact }: { offer: PayOffer; compact?: boolean }) {
  const methods: Method[] = [...(offer.donate?.length ? ['stars' as const] : []), ...(offer.donate_usd?.length ? ['crypto' as const] : [])]
  const [open, setOpen] = useState(false)
  const [method, setMethod] = useState<Method>(methods[0] ?? 'stars')
  const presets = (method === 'stars' ? offer.donate : offer.donate_usd) ?? []
  const [picks, setPicks] = useState<Record<Method, string>>({
    stars: String(offer.donate?.[1] ?? offer.donate?.[0] ?? 100),
    crypto: String(offer.donate_usd?.[1] ?? offer.donate_usd?.[0] ?? 5),
  })
  const [custom, setCustom] = useState('')
  const [busy, setBusy] = useState(false)
  const pick = picks[method]
  const max = method === 'stars' ? 10000 : 1000
  const amount = pick === CUSTOM ? Math.floor(Number(custom)) : Number(pick)
  const valid = Number.isInteger(amount) && amount >= 1 && amount <= max
  const sym = method === 'stars' ? '⭐' : '$'
  const label = (n: number) => (method === 'stars' ? `⭐ ${n}` : `$${n}`)

  async function give() {
    if (!valid || busy) return
    if (inPreview()) { toast(t('pay.preview_off')); return }
    haptic.impact('light'); setBusy(true)
    try {
      const provider = method === 'stars' ? 'donate' : 'donate_crypto'
      const inv = await api<PayInvoice>('pay', { method: 'POST', body: { provider, amount } })
      if (inv.provider === 'crypto') { openTelegramLink(inv.url); return }   // the bot thanks after the poller sees it
      const status = await openInvoice(inv.link)
      if (status === 'paid') { haptic.notify('success'); toast(t('donate.thanks')); setOpen(false) }
      else if (status !== 'cancelled') { haptic.notify('error'); toast(t('pay.failed'), 'bad') }
    } catch (e) {
      haptic.notify('error'); toast((e as Error).message, 'bad')
    } finally { setBusy(false) }
  }

  return (
    <Section title={compact ? undefined : t('donate.title')} className={compact ? 'sec--donate' : ''}
      footer={open ? t('donate.foot') : undefined}>
      <Cell icon="gift" tone="accent" title={t('donate.cell')} sub={t('donate.cell_sub')} chevron={!open}
        onClick={() => { haptic.select(); setOpen(!open) }} />
      {open && methods.length > 0 && (
        <div className="donate">
          {methods.length > 1 && (
            <Chips items={methods.map((m) => ({ id: m, label: t('donate.via_' + m) }))} value={method}
              onChange={(m) => { setMethod(m); setCustom('') }} />
          )}
          {/* the custom amount first, then the presets from small to large */}
          <Chips items={[{ id: CUSTOM, label: t('donate.custom') }, ...[...presets].sort((x, y) => x - y).map((a) => ({ id: String(a), label: label(a) }))]}
            value={pick} onChange={(v) => setPicks({ ...picks, [method]: v })} />
          {pick === CUSTOM && (
            <input className="input input--num" type="number" inputMode="numeric" min={1} max={max} value={custom}
              placeholder={sym} aria-label={t('donate.custom_aria_' + method)} onChange={(e) => setCustom(e.target.value)} />
          )}
          <Button block icon={method === 'stars' ? 'star' : 'gift'} loading={busy} disabled={!valid} onClick={() => { void give() }}>
            {valid ? t('donate.btn', { n: label(amount) }) : t('donate.btn_empty')}
          </Button>
        </div>
      )}
      {open && offer.donate_link && (
        <Cell icon="external" tone="accent" title={t('donate.link')} sub={t('donate.link_sub')} chevron
          onClick={() => { haptic.impact('light'); openLink(offer.donate_link!) }} />
      )}
    </Section>
  )
}
