import { useEffect, useState } from 'react'
import { Button, Cell, ErrorBox, Section, Skeleton, Switch, toast } from '../ui'
import { api } from '../api'
import { t } from '../i18n'
import { useApi } from '../hooks'
import { haptic, isTg } from '../tg'
import { agoShort, fmtDateTime } from '../util'
import { useApp } from '../ctx'
import Chart from './Chart'
import Integrations from './Integrations'
import Services from './Services'
import Payments from './Payments'
import DlAdmin from './DlAdmin'
import { LangSection } from '../screens/shared'
import type { ApiError } from '../api'
import type { Inbound, KpiKey, Overview as OverviewData, SyncState } from '../types'

const isAwg = (i: Inbound): boolean => i.protocol === 'wireguard' || i.protocol === 'amneziawg'

function Kpis({ counts }: { counts: OverviewData['counts'] | undefined }) {
  const { go, setAdminFilter } = useApp()
  const items: [KpiKey, string][] = [
    ['members', 'all'],
    ['active', 'active'],
    ['expiring_7d', 'soon'],
    ['expired', 'expired'],
    ['suspended', 'suspended'],
  ]
  return (
    <div className="kpis">
      {items.map(([k, f]) => (
        <button type="button" key={k} className={'kpi kpi--' + k} onClick={() => { haptic.impact('light'); setAdminFilter(f); go('tab', 'members') }}>
          <b>{counts?.[k] ?? 0}</b><span>{t('ov.k.' + k)}</span>
        </button>
      ))}
    </div>
  )
}

function Inbounds() {
  const { data, error, loading, reload, setData } = useApi<Inbound[]>('admin/inbounds')
  const [sel, setSel] = useState<number[]>([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (data) setSel(data.filter((i) => i.member).map((i) => i.id)) }, [data])

  if (loading && !data) return <Skeleton rows={4} />
  if (!data) return <ErrorBox error={error} onRetry={reload} what={t('ov.inb_load_fail')} />

  const inbounds = data   // narrowed for the handlers below
  const dirty = data.filter((i) => i.member).map((i) => i.id).sort().join() !== [...sel].sort().join()
  function toggle(i: Inbound, on: boolean) {
    setErr('')
    if (on && isAwg(i) && inbounds.some((o) => o.id !== i.id && isAwg(o) && sel.includes(o.id))) { setErr(t('ov.two_awg')); haptic.notify('error'); return }
    setSel(on ? [...sel, i.id] : sel.filter((x) => x !== i.id))
  }
  async function save() {
    setBusy(true); setErr('')
    try {
      const r = await api<Inbound[]>('admin/inbounds', { method: 'POST', body: { member_ids: sel } })
      haptic.notify('success'); toast(t('common.saved')); setData(r)
    } catch (e) { const ex = e as ApiError; haptic.notify('error'); setErr(ex.code === 'two_awg' ? t('ov.two_awg') : ex.message) } finally { setBusy(false) }
  }
  return (
    <Section title={t('ov.inb_title')} footer={t('ov.inb_foot')}>
      {data.map((i) => (
        <Cell key={i.id} icon="server" tone={i.enable ? 'accent' : 'mute'} disabled={!i.enable}
          title={i.remark || `#${i.id}`} sub={t('ov.inb_sub', { proto: i.protocol, port: i.port }) + (i.enable ? '' : ' · ' + t('ov.inb_off'))}
          right={<Switch checked={sel.includes(i.id)} disabled={!i.enable} label={i.remark} onChange={(on) => toggle(i, on)} />}
          onClick={i.enable ? () => toggle(i, !sel.includes(i.id)) : undefined} />
      ))}
      {(err || dirty) && (
        <div className="saverow">
          {err && <p className="formerr" role="alert">{err}</p>}
          <Button disabled={!dirty} loading={busy} onClick={save}>{t('common.save')}</Button>
        </div>
      )}
    </Section>
  )
}

export default function Overview() {
  const { version, openPreview, go } = useApp()
  const { data, error, loading, reload, setData } = useApi<OverviewData>('admin/overview', [version])
  const [syncing, setSyncing] = useState(false)

  async function sync() {
    haptic.impact('light'); setSyncing(true)
    try {
      const last = await api<SyncState>('admin/sync', { method: 'POST' })
      if (data) setData({ ...data, sync: last })
      haptic.notify(last.ok ? 'success' : 'error')
      toast(last.ok ? t('ov.sync_ok') : t('ov.sync_err'), last.ok ? 'ok' : 'bad')
    } catch (e) { haptic.notify('error'); toast((e as Error).message, 'bad') } finally { setSyncing(false) }
  }

  if (loading && !data) return <div className="page page--wide"><Skeleton rows={3} /><Skeleton rows={2} /></div>
  if (!data) return <div className="page page--wide"><ErrorBox error={error} onRetry={reload} what={t('ov.load_fail')} /></div>

  const s: SyncState = data.sync || {}
  return (
    <div className="page page--wide">
      <Kpis counts={data.counts} />
      <div className="cols cols--ov">
        <div>
          <section className="sec">
            <div className="sec__h"><h3>{t('ov.activity')}</h3></div>
            <div className="group group--pad"><Chart data={data.activity || []} /></div>
          </section>
          <Services />
          <Payments />
          <Inbounds />
          <DlAdmin />
        </div>
        <div>
          {data.can_tokens !== false && <Integrations />}
          <Section footer={t('ov.dl_foot')}>
            <Cell icon="download" title={t('ov.dl_title')} chevron onClick={() => { haptic.impact('light'); go('downloads') }} />
          </Section>
          {openPreview && isTg && (
            <Section footer={t('ov.pv_foot')}>
              <Cell icon="eye" title={t('pv.view_as')} chevron onClick={() => { haptic.impact('light'); openPreview() }} />
            </Section>
          )}
          <LangSection />
          <Section title={t('ov.sync_title')}
            footer={!data.vpn_configured || !data.matrix_configured
              ? t('ov.unconf', { list: [!data.vpn_configured && 'VPN', !data.matrix_configured && 'Matrix'].filter(Boolean).join(', ') }) : undefined}>
            <Cell icon="refresh" tone={s.ok === false ? 'bad' : 'accent'}
              title={s.ts ? (s.ok ? t('ov.sync_all_ok') : t('ov.sync_failed')) : t('ov.sync_never')}
              sub={s.ts ? `${fmtDateTime(s.ts)} · ${agoShort(s.ts)}` : undefined}
              value={s.vpn_clients != null ? `${s.vpn_clients} VPN` : undefined}>
              {s.error && <span className="cell__sub bad">{s.error}</span>}
            </Cell>
            <div className="cta">
              <Button kind="tonal" icon="refresh" block loading={syncing} onClick={() => { void sync() }}>{t('ov.sync_btn')}</Button>
            </div>
          </Section>
        </div>
      </div>
    </div>
  )
}
