import { useEffect, useState } from 'react'
import { Button, Cell, ErrorBox, Pill, Section, Skeleton, Switch, toast } from '../ui'
import { api } from '../api'
import { useApi } from '../hooks'
import { haptic } from '../tg'
import { t } from '../i18n'
import { fmtDateTime, plural } from '../util'
import type { PayProvider, PaymentsAdmin, PlanId } from '../types'

const fmt = (p: PayProvider, amount: number | string): string =>
  p === 'stars' ? `${Math.round(Number(amount))} ★` : `$${Number(amount).toFixed(2).replace(/\.00$/, '')}`

// Owner: Stars / CryptoBot switches, prices per term, recent payments (GET/POST /api/admin/payments).
export default function Payments() {
  const { data, error, loading, reload, setData } = useApi<PaymentsAdmin>('admin/payments')
  const [busy, setBusy] = useState('')
  const [draft, setDraft] = useState<Record<PayProvider, Record<string, string>> | null>(null)

  useEffect(() => {
    if (!data) return
    const pick = (p: PayProvider) => Object.fromEntries(Object.entries(data.settings[p].prices).map(([k, v]) => [k, String(v)]))
    setDraft({ stars: pick('stars'), crypto: pick('crypto') })
  }, [data])

  if (loading && !data) return <Skeleton rows={3} />
  if (!data || !draft) return <ErrorBox error={error} onRetry={reload} what={t('err.load')} />

  async function save(provider: PayProvider, body: { enabled?: boolean; prices?: Record<string, number> }, key: string) {
    if (busy) return
    setBusy(key)
    try {
      setData(await api<PaymentsAdmin>('admin/payments', { method: 'POST', body: { provider, ...body } }))
      haptic.notify('success'); toast(t('common.saved'))
    } catch (e) {
      haptic.notify('error'); toast((e as Error).message, 'bad')
    } finally { setBusy('') }
  }

  const row = (p: PayProvider) => {
    const on = data.settings[p].enabled
    const blocked = p === 'crypto' && !data.crypto_token
    const total = data.totals[p]
    const changed = data.plans.some((pl) => String(data.settings[p].prices[pl.id]) !== draft[p][pl.id])
    const sub = blocked ? t('pa.crypto_no_token') : t(p === 'stars' ? 'pa.stars_sub' : 'pa.crypto_sub')
    return (
      <div key={p}>
        <Cell icon={p === 'stars' ? 'star' : 'key'} tone={on ? 'accent' : 'mute'}
          title={<>{t('pa.' + p)}{p === 'crypto' && data.crypto_testnet ? <> <Pill tone="warn">{t('pa.testnet')}</Pill></> : null}</>}
          sub={sub}
          right={<Switch checked={on} disabled={blocked || busy !== ''} label={t('pa.' + p)}
            onChange={(v) => { void save(p, { enabled: v }, p + ':sw') }} />}>
          {total && <span className="cell__sub">{t('pa.total', { n: total.count, unit: plural(total.count, t('pa.unit_pay').split('|')), sum: fmt(p, total.amount) })}</span>}
        </Cell>
        {!blocked && (
          <div className="payprices">
            <p className="form__l">{t('pa.prices')}{p === 'stars' ? ', ★' : ', $'}</p>
            <div className="payprices__row">
              {data.plans.map((pl) => (
                <label key={pl.id} className="payprices__f">
                  <span>{t('pay.plan.' + pl.id)}</span>
                  <input className="input input--num" type="number" inputMode="decimal" min={p === 'stars' ? 1 : 0.1}
                    step={p === 'stars' ? 1 : 0.5} value={draft[p][pl.id] ?? ''}
                    onChange={(e) => setDraft({ ...draft, [p]: { ...draft[p], [pl.id]: e.target.value } })} />
                </label>
              ))}
            </div>
            {changed && (
              <Button kind="tonal" size="s" loading={busy === p + ':pr'}
                onClick={() => {
                  const prices: Partial<Record<PlanId, number>> = {}
                  for (const pl of data.plans) prices[pl.id] = Number(draft[p][pl.id])
                  void save(p, { prices }, p + ':pr')
                }}>{t('pa.save')}</Button>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <>
      <Section title={t('pa.title')} footer={t('pa.foot')}>
        {row('stars')}
        {row('crypto')}
      </Section>
      <Section title={t('pa.recent')}>
        {data.recent.length === 0 && <Cell icon="info" tone="mute" title={t('pa.none')} />}
        {data.recent.map((r) => (
          <Cell key={r.id} icon={r.provider === 'stars' ? 'star' : 'key'} tone={r.refunded ? 'mute' : 'ok'}
            title={r.name || String(r.uid)} sub={`${t('pay.plan.' + r.plan)} · ${fmtDateTime(r.ts)}`}
            value={fmt(r.provider, r.amount)}
            right={r.refunded ? <Pill tone="mute">{t('pa.refunded')}</Pill> : undefined} />
        ))}
      </Section>
    </>
  )
}
