import { useEffect, useState } from 'react'
import Icon from '../icons.jsx'
import { Avatar, Button, Cell, Dot, ErrorBox, Field, Section, Skeleton, toast } from '../ui.jsx'
import { api } from '../api.js'
import { hasKey, t } from '../i18n.js'
import { useApi } from '../hooks.js'
import { confirmDialog, haptic } from '../tg.js'
import { ago, fmtBytes, fmtDate, fmtDateTime, personName } from '../util.js'
import { useApp } from '../ctx.js'
import { Title } from '../screens/shared.jsx'
import { ServiceToggles, StatusPill } from './shared.jsx'

const evLabel = (kind) => (hasKey('ev.' + kind) ? t('ev.' + kind) : kind)

function dayEndTs(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return Math.floor(new Date(y, m - 1, d, 23, 59, 59).getTime() / 1000)
}

export default function MemberDetail({ uid }) {
  const { back, bump, version } = useApp()
  const { data: m, error, loading, reload } = useApi('admin/members/' + uid, [version])
  const [busy, setBusy] = useState('')
  const [date, setDate] = useState('')
  const [note, setNote] = useState('')
  const [services, setServices] = useState([])

  useEffect(() => { if (m) { setNote(m.note || ''); setServices(m.services || []) } }, [m])

  if (loading && !m) return <div className="page page--wide"><Title>{t('md.title')}</Title><Skeleton rows={3} /><Skeleton rows={3} /></div>
  if (error && !m) return <div className="page page--wide"><Title>{t('md.title')}</Title><ErrorBox error={error} onRetry={reload} what={t('md.load_fail')} /></div>

  async function act(body, okText, key = body.action) {
    setBusy(key)
    try {
      const r = await api('admin/members/' + uid, { method: 'POST', body })
      haptic.notify('success')
      if (okText) toast(okText)
      bump()
      if (r?.deleted) { back(); return }
      reload()
    } catch (e) {
      haptic.notify('error'); toast(e.message, 'bad')
      reload()
    } finally { setBusy('') }
  }

  async function ask(text, then) { if (await confirmDialog(text)) then() }
  const name = personName(m)
  const suspended = m.status === 'suspended'
  const today = new Date().toISOString().slice(0, 10)

  return (
    <div className="page page--wide">
      <Title>{' '}</Title>
      <div className="mhead">
        <Avatar uid={m.id} member={m} size={96} />
        <h2>{name}</h2>
        <p>{m.username ? '@' + m.username + ' · ' : ''}ID {m.id}</p>
        <div className="mhead__s">
          <StatusPill m={m} />
          <span>{m.expires_ts == null ? t('pass.no_expiry') : t('md.until', { date: fmtDate(m.expires_ts, true) }) + (m.days_left != null && m.status === 'active' ? ' · ' + t('left.days', { n: m.days_left }) : '')}</span>
        </div>
        <p className="hint">{ago(m.last_seen_ts)}</p>
      </div>

      <div className="cols">
        <div>
          <Section title={t('md.sub')}>
            <Cell icon="plus" title={t('md.plus30')} onClick={() => { haptic.impact('light'); act({ action: 'extend', days: 30 }, t('md.ext30'), 'e30') }} right={busy === 'e30' ? <span className="spin" /> : null} />
            <Cell icon="plus" title={t('md.plus90')} onClick={() => { haptic.impact('light'); act({ action: 'extend', days: 90 }, t('md.ext90'), 'e90') }} right={busy === 'e90' ? <span className="spin" /> : null} />
            <div className="cell cell--lead cell--datepick">
              <span className="tile tile--accent"><Icon name="calendar" size={18} /></span>
              <input className="input input--date" type="date" min={today} value={date} onChange={(e) => setDate(e.target.value)} aria-label={t('md.date_aria')} />
              <Button kind="tonal" size="s" disabled={!date} loading={busy === 'date'}
                onClick={() => act({ action: 'set_expiry', ts: dayEndTs(date) }, t('ev.set_expiry'), 'date')}>{t('md.set')}</Button>
            </div>
            <Cell icon="infinity" title={t('md.forever')} onClick={() => ask(t('md.forever_q'), () => act({ action: 'set_expiry', ts: null }, t('md.forever_done'), 'forever'))} />
            {suspended
              ? <Cell icon="play" tone="ok" title={t('md.resume')} onClick={() => ask(t('md.resume_q', { name }), () => act({ action: 'resume' }, t('md.resumed')))} />
              : <Cell icon="pause" tone="warn" title={t('md.suspend')} onClick={() => ask(t('md.suspend_q', { name }), () => act({ action: 'suspend' }, t('md.suspended')))} />}
          </Section>

          <Section title={t('md.services')} footer={t('md.services_foot')}>
            <ServiceToggles value={services} disabled={busy === 'services'}
              onChange={(v) => { setServices(v); act({ action: 'services', services: v }, t('md.services_done')) }} />
          </Section>

          <Section title={t('md.note')}>
            <div className="notebox">
              <textarea className="input" rows={3} maxLength={500} value={note} placeholder={t('md.note_ph')} onChange={(e) => setNote(e.target.value)} />
              {note !== (m.note || '') && <Button kind="tonal" size="s" loading={busy === 'note'} onClick={() => act({ action: 'note', note }, t('md.note_saved'))}>{t('common.save')}</Button>}
            </div>
          </Section>
        </div>

        <div>
          <Section title="Matrix" footer={m.matrix_accounts?.length ? undefined : t('md.no_accounts')}>
            {(m.matrix_accounts || []).map((a) => (
              <Cell key={a.mxid} icon={a.locked ? 'lock' : 'chat'} tone={a.locked ? 'mute' : 'accent'} title={<span className="mono">{a.mxid}</span>}
                sub={t('md.created', { date: fmtDate(a.created_ts, true) })} value={a.locked ? t('mx.locked') : t('md.active')} />
            ))}
          </Section>

          <Section title="VPN">
            <Cell icon="shield" title={t('md.xui_client')} value={m.has_vpn_client ? t('md.yes') : t('md.no')}
              sub={m.vpn_email} right={<Dot on={m.online} />} />
            <Cell icon="down" title={t('vpn.down')} value={fmtBytes(m.traffic?.down)} />
            <Cell icon="up" title={t('vpn.up')} value={fmtBytes(m.traffic?.up)} />
            {m.vpn_links_count != null && <Cell icon="link" title={t('md.cfg_count')} value={m.vpn_links_count} />}
          </Section>

          <Section title={t('md.events')}>
            {(m.events || []).length === 0 && <Cell icon="clock" tone="mute" title={t('md.no_events')} />}
            {(m.events || []).map((e, i) => (
              <Cell key={e.id ?? i} icon="clock" tone="mute" title={evLabel(e.kind)} sub={e.detail || undefined} value={fmtDateTime(e.ts)} />
            ))}
          </Section>
        </div>
      </div>

      <Section footer={t('md.delete_foot')}>
        <Cell icon="trash" tone="bad" danger title={t('md.delete')}
          onClick={async () => {
            if (!(await confirmDialog(t('md.delete_q', { name })))) return
            if (await confirmDialog(t('md.delete_q2'))) act({ action: 'delete' }, t('md.deleted'))
          }} />
      </Section>
    </div>
  )
}
