import { useState } from 'react'
import { Button, Cell, Chips, Section, toast } from '../ui'
import { api, inPreview } from '../api'
import { t } from '../i18n'
import { haptic, openInvoice } from '../tg'
import type { PayInvoice } from '../types'

const CUSTOM = 'custom'

// "Support the project": a free amount in Telegram Stars. Shown only inside Telegram while the
// owner has donations (and Stars) switched on; it never touches the member's term.
export default function Donate({ amounts }: { amounts: number[] }) {
  const [open, setOpen] = useState(false)
  const [pick, setPick] = useState(String(amounts[1] ?? amounts[0] ?? 100))
  const [custom, setCustom] = useState('')
  const [busy, setBusy] = useState(false)
  const amount = pick === CUSTOM ? Math.floor(Number(custom)) : Number(pick)
  const valid = Number.isInteger(amount) && amount >= 1 && amount <= 10000

  async function give() {
    if (!valid || busy) return
    if (inPreview()) { toast(t('pay.preview_off')); return }
    haptic.impact('light'); setBusy(true)
    try {
      const inv = await api<PayInvoice>('pay', { method: 'POST', body: { provider: 'donate', amount } })
      if (inv.provider !== 'stars') return
      const status = await openInvoice(inv.link)
      if (status === 'paid') { haptic.notify('success'); toast(t('donate.thanks')); setOpen(false) }
      else if (status !== 'cancelled') { haptic.notify('error'); toast(t('pay.failed'), 'bad') }
    } catch (e) {
      haptic.notify('error'); toast((e as Error).message, 'bad')
    } finally { setBusy(false) }
  }

  return (
    <Section title={t('donate.title')} footer={open ? t('donate.foot') : undefined}>
      <Cell icon="gift" tone="accent" title={t('donate.cell')} sub={t('donate.cell_sub')} chevron={!open}
        onClick={() => { haptic.select(); setOpen(!open) }} />
      {open && (
        <div className="donate">
          {/* the custom amount first, then the presets from small to large */}
          <Chips items={[{ id: CUSTOM, label: t('donate.custom') }, ...[...amounts].sort((x, y) => x - y).map((a) => ({ id: String(a), label: `⭐ ${a}` }))]}
            value={pick} onChange={setPick} />
          {pick === CUSTOM && (
            <input className="input input--num" type="number" inputMode="numeric" min={1} max={10000} value={custom}
              placeholder="⭐" aria-label={t('donate.custom_aria')} onChange={(e) => setCustom(e.target.value)} />
          )}
          <Button block icon="star" loading={busy} disabled={!valid} onClick={() => { void give() }}>
            {valid ? t('donate.btn', { n: amount }) : t('donate.btn_empty')}
          </Button>
        </div>
      )}
    </Section>
  )
}
