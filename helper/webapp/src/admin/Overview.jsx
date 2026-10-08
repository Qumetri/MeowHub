import { useEffect, useState } from 'react'
import Icon from '../icons.jsx'
import { Button, Cell, ErrorBox, Section, Skeleton, Switch, toast } from '../ui.jsx'
import { api } from '../api.js'
import { t } from '../i18n.js'
import { useApi } from '../hooks.js'
import { haptic, isTg } from '../tg.js'
import { agoShort, fmtDateTime } from '../util.js'
import { useApp } from '../ctx.js'
import Chart from './Chart.jsx'
import Integrations from './Integrations.jsx'
import Services from './Services.jsx'
import DlAdmin from './DlAdmin.jsx'
import { LangSection } from '../screens/shared.jsx'

const isAwg = (i) => i.protocol === 'wireguard' || i.protocol === 'amneziawg'

function Kpis({ counts }) {
  const { go, setAdminFilter } = useApp()
  const items = [
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
  const { data, error, loading, reload, setData } = useApi('admin/inbounds')
  const [sel, setSel] = useState([])
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => { if (data) setSel(data.filter((i) => i.member).map((i) => i.id)) }, [data])

  if (loading && !data) return <Skeleton rows={4} />
  if (error && !data) return <ErrorBox error={error} onRetry={reload} what={t('ov.inb_load_fail')} />

  const dirty = data.filter((i) => i.member).map((i) => i.id).sort().join() !== [...sel].sort().join()
  function toggle(i, on) {
    setErr('')
    if (on && isAwg(i) && data.some((o) => o.id !== i.id && isAwg(o) && sel.includes(o.id))) { setErr(t('ov.two_awg')); haptic.notify('error'); return }
    setSel(on ? [...sel, i.id] : sel.filter((x) => x !== i.id))
  }
  async function save() {
    setBusy(true); setErr('')
    try {
      const r = await api('admin/inbounds', { method: 'POST', body: { member_ids: sel } })
      haptic.notify('success'); toast(t('common.saved')); setData(r)
    } catch (e) { haptic.notify('error'); setErr(e.code === 'two_awg' ? t('ov.two_awg') : e.message) } finally { setBusy(false) }
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
  const { data, error, loading, reload, setData } = useApi('admin/overview', [version])
  const [syncing, setSyncing] = useState(false)

  async function sync() {
    haptic.impact('light'); setSyncing(true)
    try {
      const last = await api('admin/sync', { method: 'POST' })
      setData({ ...data, sync: last })
      haptic.notify(last.ok ? 'success' : 'error')
      toast(last.ok ? t('ov.sync_ok') : t('ov.sync_err'), last.ok ? 'ok' : 'bad')
    } catch (e) { haptic.notify('error'); toast(e.message, 'bad') } finally { setSyncing(false) }
  }

  if (loading && !data) return <div className="page page--wide"><Skeleton rows={3} /><Skeleton rows={2} /></div>
  if (error && !data) return <div className="page page--wide"><ErrorBox error={error} onRetry={reload} what={t('ov.load_fail')} /></div>

  const s = data.sync || {}
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
          <Inbounds />
          <DlAdmin />
        </div>
        <div>
          <Integrations />
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
              <Button kind="tonal" icon="refresh" block loading={syncing} onClick={sync}>{t('ov.sync_btn')}</Button>
            </div>
          </Section>
        </div>
      </div>
    </div>
  )
}
