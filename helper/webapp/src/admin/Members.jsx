import { useMemo, useState } from 'react'
import Icon from '../icons.jsx'
import { Avatar, Button, Cell, Chips, Empty, ErrorBox, Field, Section, Sheet, Skeleton, toast } from '../ui.jsx'
import { api } from '../api.js'
import { useApi, useMedia } from '../hooks.js'
import { haptic, isTg } from '../tg.js'
import { ago, fmtBytes, personName } from '../util.js'
import { useApp } from '../ctx.js'
import { DayPicker, ServiceIcons, ServiceToggles, StatusPill, leftLabel } from './shared.jsx'

export const FILTERS = [
  ['all', 'Все', () => true],
  ['active', 'Активные', (m) => m.status === 'active'],
  ['soon', 'Истекают ≤7д', (m) => m.status === 'active' && m.days_left != null && m.days_left <= 7],
  ['expired', 'Истекли', (m) => m.status === 'expired'],
  ['suspended', 'Приостановлены', (m) => m.status === 'suspended'],
]

function GrantSheet({ open, onClose, onDone }) {
  const [uid, setUid] = useState('')
  const [days, setDays] = useState(30)
  const [services, setServices] = useState(['vpn', 'matrix'])
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const ok = /^\d{3,15}$/.test(uid.trim()) && days > 0
  async function go() {
    setBusy(true); setErr('')
    try {
      const m = await api('admin/grant', { method: 'POST', body: { uid: Number(uid.trim()), days, services } })
      haptic.notify('success'); toast('Доступ выдан'); setUid(''); onDone(m)
    } catch (e) { haptic.notify('error'); setErr(e.message) } finally { setBusy(false) }
  }
  return (
    <Sheet open={open} title="Выдать доступ по ID" onClose={onClose}>
      <Field label="Telegram ID" hint="Числовой ID человека. Он получит уведомление, когда откроет бота." error={err}>
        <input className="input" inputMode="numeric" value={uid} placeholder="123456789" onChange={(e) => { setUid(e.target.value); setErr('') }} />
      </Field>
      <p className="form__l">Срок, дней</p>
      <DayPicker value={days} onChange={setDays} />
      <p className="form__l">Сервисы</p>
      <Section><ServiceToggles value={services} onChange={setServices} /></Section>
      <Button block icon="plus" disabled={!ok} loading={busy} onClick={go}>Выдать доступ</Button>
    </Sheet>
  )
}

function Table({ rows, onOpen }) {
  return (
    <div className="group tbl" role="table">
      <div className="tbl__row tbl__head" role="row">
        <span>Участник</span><span>Статус</span><span>Осталось</span><span>Сервисы</span><span>Трафик</span><span>Был(а)</span>
      </div>
      {rows.map((m) => (
        <div key={m.id} className="tbl__row tbl__body" role="row" tabIndex={0} onClick={() => onOpen(m)}
          onKeyDown={(e) => { if (e.key === 'Enter') onOpen(m) }}>
          <span className="tbl__who">
            <Avatar uid={m.id} member={m} size={36} />
            <span><b>{personName(m)}</b><small>{m.username ? '@' + m.username : 'ID ' + m.id}</small></span>
          </span>
          <span><StatusPill m={m} /></span>
          <span className="num">{leftLabel(m)}</span>
          <span><ServiceIcons services={m.services} /></span>
          <span className="num">{m.traffic ? `↓${fmtBytes(m.traffic.down)}` : '—'}{m.online && <i className="dot on" />}</span>
          <span className="hint">{ago(m.last_seen_ts)}</span>
        </div>
      ))}
    </div>
  )
}

export default function Members() {
  const { go, version, bump, adminFilter, setAdminFilter } = useApp()
  const { data, error, loading, reload } = useApi('admin/members', [version])
  const [q, setQ] = useState('')
  const [grant, setGrant] = useState(false)
  const wide = useMedia('(min-width: 860px)') && !isTg
  const filter = adminFilter

  const rows = useMemo(() => {
    const f = FILTERS.find((x) => x[0] === filter)[2]
    const needle = q.trim().toLowerCase().replace(/^@/, '')
    return (data || []).filter(f).filter((m) =>
      !needle || `${m.first_name} ${m.last_name} ${m.username} ${m.id}`.toLowerCase().includes(needle))
  }, [data, filter, q])
  const counts = useMemo(() => Object.fromEntries(FILTERS.map(([id, , f]) => [id, (data || []).filter(f).length])), [data])

  const open = (m) => { haptic.impact('light'); go('member', { uid: m.id }) }

  return (
    <div className="page page--wide">
      <div className="toolbar">
        <label className="search">
          <Icon name="search" size={18} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Имя, @username или ID" aria-label="Поиск" />
        </label>
        <Button kind="tonal" icon="plus" onClick={() => { haptic.impact('light'); setGrant(true) }}>Выдать доступ по ID</Button>
      </div>
      <Chips items={FILTERS.map(([id, label]) => ({ id, label, count: data ? counts[id] : undefined }))} value={filter} onChange={setAdminFilter} />

      {data?.warning && (
        <div className="warnbox"><Icon name="warning" size={18} />
          <span>{data.warning === 'vpn_unconfigured' ? 'VPN-панель не настроена — трафик и статус «онлайн» недоступны.' : 'VPN-панель не отвечает — трафик и статус «онлайн» недоступны.'}</span></div>
      )}
      {loading && !data && <Skeleton rows={5} title={false} />}
      {error && !data && <ErrorBox error={error} onRetry={reload} what="Не удалось загрузить участников" />}
      {data && rows.length === 0 && (
        <Empty icon="users" title={data.length ? 'Никого не нашли' : 'Участников пока нет'}
          text={data.length ? 'Измени поиск или фильтр.' : 'Создай код приглашения или выдай доступ по ID.'}
          action={data.length ? null : <Button icon="ticket" onClick={() => go('tab', 'codes')}>Создать код</Button>} />
      )}
      {data && rows.length > 0 && (wide
        ? <Table rows={rows} onOpen={open} />
        : (
          <div className="group">
            {rows.map((m) => (
              <Cell key={m.id} lead={<Avatar uid={m.id} member={m} size={44} />} className="cell--member"
                title={<span className="mname">{personName(m)}</span>}
                sub={`${m.username ? '@' + m.username + ' · ' : ''}${ago(m.last_seen_ts)}`}
                right={
                  <span className="mrow__r">
                    <StatusPill m={m} />
                    <span className="mrow__meta"><b>{leftLabel(m)}</b><ServiceIcons services={m.services} /></span>
                  </span>
                }
                onClick={() => open(m)} />
            ))}
          </div>
        ))}

      <GrantSheet open={grant} onClose={() => setGrant(false)}
        onDone={(m) => { setGrant(false); bump(); go('member', { uid: m.id }) }} />
    </div>
  )
}
