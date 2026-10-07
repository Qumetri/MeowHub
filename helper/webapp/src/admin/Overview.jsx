import { useEffect, useState } from 'react'
import Icon from '../icons.jsx'
import { Button, Cell, ErrorBox, Section, Skeleton, Switch, toast } from '../ui.jsx'
import { api } from '../api.js'
import { useApi } from '../hooks.js'
import { haptic, isTg } from '../tg.js'
import { ago, fmtDateTime } from '../util.js'
import { useApp } from '../ctx.js'
import Chart from './Chart.jsx'
import Integrations from './Integrations.jsx'

const TWO_AWG = 'Нельзя выдавать два AmneziaWG/WireGuard инбаунда одновременно'
const isAwg = (i) => i.protocol === 'wireguard' || i.protocol === 'amneziawg'

function Kpis({ counts }) {
  const { go, setAdminFilter } = useApp()
  const items = [
    ['members', 'Участников', 'all'],
    ['active', 'Активных', 'active'],
    ['expiring_7d', 'Истекают за 7 дней', 'soon'],
    ['expired', 'Истекли', 'expired'],
    ['suspended', 'Приостановлены', 'suspended'],
  ]
  return (
    <div className="kpis">
      {items.map(([k, label, f]) => (
        <button type="button" key={k} className={'kpi kpi--' + k} onClick={() => { haptic.impact('light'); setAdminFilter(f); go('tab', 'members') }}>
          <b>{counts?.[k] ?? 0}</b><span>{label}</span>
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
  if (error && !data) return <ErrorBox error={error} onRetry={reload} what="Не удалось загрузить инбаунды" />

  const dirty = data.filter((i) => i.member).map((i) => i.id).sort().join() !== [...sel].sort().join()
  function toggle(i, on) {
    setErr('')
    if (on && isAwg(i) && data.some((o) => o.id !== i.id && isAwg(o) && sel.includes(o.id))) { setErr(TWO_AWG); haptic.notify('error'); return }
    setSel(on ? [...sel, i.id] : sel.filter((x) => x !== i.id))
  }
  async function save() {
    setBusy(true); setErr('')
    try {
      const r = await api('admin/inbounds', { method: 'POST', body: { member_ids: sel } })
      haptic.notify('success'); toast('Сохранено'); setData(r)
    } catch (e) { haptic.notify('error'); setErr(e.code === 'two_awg' ? TWO_AWG : e.message) } finally { setBusy(false) }
  }
  return (
    <Section title="VPN-инбаунды участников" footer="Участники получают новые инбаунды автоматически через ссылку подписки.">
      {data.map((i) => (
        <Cell key={i.id} icon="server" tone={i.enable ? 'accent' : 'mute'} disabled={!i.enable}
          title={i.remark || `#${i.id}`} sub={`${i.protocol} · порт ${i.port}${i.enable ? '' : ' · выключен'}`}
          right={<Switch checked={sel.includes(i.id)} disabled={!i.enable} label={i.remark} onChange={(on) => toggle(i, on)} />}
          onClick={i.enable ? () => toggle(i, !sel.includes(i.id)) : undefined} />
      ))}
      {(err || dirty) && (
        <div className="saverow">
          {err && <p className="formerr" role="alert">{err}</p>}
          <Button disabled={!dirty} loading={busy} onClick={save}>Сохранить</Button>
        </div>
      )}
    </Section>
  )
}

export default function Overview() {
  const { version, openPreview } = useApp()
  const { data, error, loading, reload, setData } = useApi('admin/overview', [version])
  const [syncing, setSyncing] = useState(false)

  async function sync() {
    haptic.impact('light'); setSyncing(true)
    try {
      const last = await api('admin/sync', { method: 'POST' })
      setData({ ...data, sync: last })
      haptic.notify(last.ok ? 'success' : 'error')
      toast(last.ok ? 'Синхронизировано' : 'Синхронизация с ошибкой', last.ok ? 'ok' : 'bad')
    } catch (e) { haptic.notify('error'); toast(e.message, 'bad') } finally { setSyncing(false) }
  }

  if (loading && !data) return <div className="page page--wide"><Skeleton rows={3} /><Skeleton rows={2} /></div>
  if (error && !data) return <div className="page page--wide"><ErrorBox error={error} onRetry={reload} what="Не удалось загрузить обзор" /></div>

  const s = data.sync || {}
  return (
    <div className="page page--wide">
      <Kpis counts={data.counts} />
      <div className="cols cols--ov">
        <div>
          <section className="sec">
            <div className="sec__h"><h3>Активность за 30 дней</h3></div>
            <div className="group group--pad"><Chart data={data.activity || []} /></div>
          </section>
          <Inbounds />
        </div>
        <div>
          <Integrations />
          {openPreview && isTg && (
            <Section footer="Экран гостя, участника и участника с истёкшим сроком — на тестовых данных.">
              <Cell icon="eye" title="Посмотреть как участник" chevron onClick={() => { haptic.impact('light'); openPreview() }} />
            </Section>
          )}
          <Section title="Синхронизация"
            footer={!data.vpn_configured || !data.matrix_configured
              ? `Не настроено: ${[!data.vpn_configured && 'VPN', !data.matrix_configured && 'Matrix'].filter(Boolean).join(', ')}.` : undefined}>
            <Cell icon="refresh" tone={s.ok === false ? 'bad' : 'accent'}
              title={s.ts ? (s.ok ? 'Всё синхронизировано' : 'Ошибка синхронизации') : 'Ещё не запускалась'}
              sub={s.ts ? `${fmtDateTime(s.ts)} · ${ago(s.ts).replace(/^был\(а\) /, '')}` : undefined}
              value={s.vpn_clients != null ? `${s.vpn_clients} VPN` : undefined}>
              {s.error && <span className="cell__sub bad">{s.error}</span>}
            </Cell>
            <div className="cta">
              <Button kind="tonal" icon="refresh" block loading={syncing} onClick={sync}>Синхронизировать</Button>
            </div>
          </Section>
        </div>
      </div>
    </div>
  )
}
