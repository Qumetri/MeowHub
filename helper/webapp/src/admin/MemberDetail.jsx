import { useEffect, useState } from 'react'
import Icon from '../icons.jsx'
import { Avatar, Button, Cell, Dot, ErrorBox, Field, Section, Skeleton, toast } from '../ui.jsx'
import { api } from '../api.js'
import { useApi } from '../hooks.js'
import { confirmDialog, haptic } from '../tg.js'
import { ago, fmtBytes, fmtDate, fmtDateTime, personName } from '../util.js'
import { useApp } from '../ctx.js'
import { Title } from '../screens/shared.jsx'
import { ServiceToggles, StatusPill } from './shared.jsx'

const EV = {
  redeem: 'Код активирован', grant: 'Доступ выдан', extend: 'Срок продлён', set_expiry: 'Срок изменён',
  expiry: 'Срок изменён', suspend: 'Приостановлен', resume: 'Возобновлён', services: 'Сервисы изменены',
  note: 'Заметка', delete: 'Удалён', matrix: 'Matrix-аккаунт', matrix_create: 'Matrix-аккаунт создан',
  expired: 'Подписка истекла', reminder: 'Напоминание', join: 'Первый вход',
}

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

  if (loading && !m) return <div className="page page--wide"><Title>Участник</Title><Skeleton rows={3} /><Skeleton rows={3} /></div>
  if (error && !m) return <div className="page page--wide"><Title>Участник</Title><ErrorBox error={error} onRetry={reload} what="Не удалось загрузить участника" /></div>

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
          <span>{m.expires_ts == null ? 'без срока' : `до ${fmtDate(m.expires_ts, true)}${m.days_left != null && m.status === 'active' ? ` · ${m.days_left} д` : ''}`}</span>
        </div>
        <p className="hint">{ago(m.last_seen_ts)}</p>
      </div>

      <div className="cols">
        <div>
          <Section title="Подписка">
            <Cell icon="plus" title="+30 дней" onClick={() => { haptic.impact('light'); act({ action: 'extend', days: 30 }, 'Продлено на 30 дней', 'e30') }} right={busy === 'e30' ? <span className="spin" /> : null} />
            <Cell icon="plus" title="+90 дней" onClick={() => { haptic.impact('light'); act({ action: 'extend', days: 90 }, 'Продлено на 90 дней', 'e90') }} right={busy === 'e90' ? <span className="spin" /> : null} />
            <div className="cell cell--lead cell--datepick">
              <span className="tile tile--accent"><Icon name="calendar" size={18} /></span>
              <input className="input input--date" type="date" min={today} value={date} onChange={(e) => setDate(e.target.value)} aria-label="Дата окончания" />
              <Button kind="tonal" size="s" disabled={!date} loading={busy === 'date'}
                onClick={() => act({ action: 'set_expiry', ts: dayEndTs(date) }, 'Срок изменён', 'date')}>Задать</Button>
            </div>
            <Cell icon="infinity" title="Без срока" onClick={() => ask('Убрать срок окончания? Подписка станет бессрочной.', () => act({ action: 'set_expiry', ts: null }, 'Срок убран', 'forever'))} />
            {suspended
              ? <Cell icon="play" tone="ok" title="Возобновить" onClick={() => ask(`Возобновить доступ для ${name}?`, () => act({ action: 'resume' }, 'Возобновлено'))} />
              : <Cell icon="pause" tone="warn" title="Приостановить" onClick={() => ask(`Приостановить доступ для ${name}? VPN и Matrix-аккаунты отключатся.`, () => act({ action: 'suspend' }, 'Приостановлено'))} />}
          </Section>

          <Section title="Сервисы" footer="Переключатель применяется сразу.">
            <ServiceToggles value={services} disabled={busy === 'services'}
              onChange={(v) => { setServices(v); act({ action: 'services', services: v }, 'Сервисы обновлены') }} />
          </Section>

          <Section title="Заметка">
            <div className="notebox">
              <textarea className="input" rows={3} maxLength={500} value={note} placeholder="Видна только тебе" onChange={(e) => setNote(e.target.value)} />
              {note !== (m.note || '') && <Button kind="tonal" size="s" loading={busy === 'note'} onClick={() => act({ action: 'note', note }, 'Заметка сохранена')}>Сохранить</Button>}
            </div>
          </Section>
        </div>

        <div>
          <Section title="Matrix" footer={m.matrix_accounts?.length ? undefined : 'Аккаунтов нет'}>
            {(m.matrix_accounts || []).map((a) => (
              <Cell key={a.mxid} icon={a.locked ? 'lock' : 'chat'} tone={a.locked ? 'mute' : 'accent'} title={<span className="mono">{a.mxid}</span>}
                sub={`создан ${fmtDate(a.created_ts, true)}`} value={a.locked ? 'заблокирован' : 'активен'} />
            ))}
          </Section>

          <Section title="VPN">
            <Cell icon="shield" title="Клиент в 3x-ui" value={m.has_vpn_client ? 'есть' : 'нет'}
              sub={m.vpn_email} right={<Dot on={m.online} />} />
            <Cell icon="down" title="Получено" value={fmtBytes(m.traffic?.down)} />
            <Cell icon="up" title="Отправлено" value={fmtBytes(m.traffic?.up)} />
            {m.vpn_links_count != null && <Cell icon="link" title="Конфигов в подписке" value={m.vpn_links_count} />}
          </Section>

          <Section title="События">
            {(m.events || []).length === 0 && <Cell icon="clock" tone="mute" title="Событий пока нет" />}
            {(m.events || []).map((e, i) => (
              <Cell key={e.id ?? i} icon="clock" tone="mute" title={EV[e.kind] || e.kind} sub={e.detail || undefined} value={fmtDateTime(e.ts)} />
            ))}
          </Section>
        </div>
      </div>

      <Section footer="Участник, его VPN-клиент и доступ будут удалены. Matrix-аккаунты останутся заблокированными.">
        <Cell icon="trash" tone="bad" danger title="Удалить участника"
          onClick={async () => {
            if (!(await confirmDialog(`Удалить ${name}?`))) return
            if (await confirmDialog('Точно удалить? Это действие нельзя отменить.')) act({ action: 'delete' }, 'Участник удалён')
          }} />
      </Section>
    </div>
  )
}
