import { useState } from 'react'
import Icon from '../icons.jsx'
import { Button, Cell, Collapse, Empty, ErrorBox, Field, Pill, Section, Skeleton, doCopy, toast } from '../ui.jsx'
import { api } from '../api.js'
import { useApi } from '../hooks.js'
import { confirmDialog, haptic, isTg, openTelegramLink } from '../tg.js'
import { daysWord, fmtDate } from '../util.js'
import { useApp } from '../ctx.js'
import { DayPicker, SVC, ServiceToggles } from './shared.jsx'

function share(url) {
  if (isTg) openTelegramLink('https://t.me/share/url?url=' + encodeURIComponent(url))
  else doCopy(url)
}

function Fresh({ fresh, onClose }) {
  return (
    <div className="fresh" role="status">
      <p className="fresh__l">Новый код</p>
      <p className="fresh__code">{fresh.code}</p>
      <div className="fresh__b">
        <Button kind="tonal" icon="copy" onClick={() => { haptic.impact('light'); doCopy(fresh.code) }}>Копировать</Button>
        <Button icon={isTg ? 'send' : 'link'} onClick={() => { haptic.impact('light'); share(fresh.share_url) }}>
          {isTg ? 'Поделиться' : 'Копировать ссылку'}
        </Button>
        <Button kind="plain" onClick={onClose}>Скрыть</Button>
      </div>
    </div>
  )
}

function CodeCell({ c, onRevoke }) {
  const names = (c.services || []).map((s) => SVC[s]?.name || s).join(', ')
  const live = c.state === 'live'
  const stateTone = { used: 'mute', expired: 'warn', revoked: 'bad' }[c.state]
  const stateText = { used: 'использован', expired: 'истёк', revoked: 'отозван' }[c.state]
  return (
    <Cell icon="ticket" tone={live ? 'accent' : 'mute'} title={<span className="mono">{c.code}</span>}
      sub={`${c.days} ${daysWord(c.days)} · ${names} · использований ${c.uses}/${c.uses_max}${c.note ? ' · ' + c.note : ''}`}
      right={
        <span className="cell__r">
          {live
            ? (
              <>
                <button type="button" className="iconbtn" aria-label="Скопировать ссылку" onClick={() => { haptic.impact('light'); share(c.share_url) }}>
                  <Icon name={isTg ? 'send' : 'link'} size={18} />
                </button>
                <button type="button" className="iconbtn iconbtn--danger" aria-label="Отозвать код" onClick={() => onRevoke(c)}>
                  <Icon name="trash" size={18} />
                </button>
              </>
            )
            : <Pill tone={stateTone}>{stateText}</Pill>}
        </span>
      }>
      {live && <span className="cell__sub">действует до {fmtDate(c.valid_until, true)}</span>}
    </Cell>
  )
}

export default function Codes() {
  const { version, bump } = useApp()
  const { data, error, loading, reload } = useApi('admin/codes', [version])
  const [services, setServices] = useState(['vpn', 'matrix'])
  const [days, setDays] = useState(30)
  const [uses, setUses] = useState(1)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [fresh, setFresh] = useState(null)
  const [formOpen, setFormOpen] = useState(true)

  async function create() {
    setBusy(true)
    try {
      const r = await api('admin/codes', { method: 'POST', body: { days, services, uses_max: uses, note } })
      haptic.notify('success'); setFresh(r); setNote(''); bump(); reload()
    } catch (e) { haptic.notify('error'); toast(e.message, 'bad') } finally { setBusy(false) }
  }
  async function revoke(c) {
    if (!(await confirmDialog(`Отозвать код ${c.code}?`))) return
    try {
      await api(`admin/codes/${encodeURIComponent(c.code)}/revoke`, { method: 'POST' })
      haptic.notify('success'); toast('Код отозван'); reload()
    } catch (e) { haptic.notify('error'); toast(e.message, 'bad') }
  }

  const live = (data || []).filter((c) => c.state === 'live')
  const dead = (data || []).filter((c) => c.state !== 'live')
  const valid = services.length > 0 && days > 0 && uses > 0

  return (
    <div className="page page--wide">
      <div className="cols">
        <div>
          {fresh && <Fresh fresh={fresh} onClose={() => setFresh(null)} />}
          <section className="sec">
            <button type="button" className="sec__h sec__h--btn" aria-expanded={formOpen} onClick={() => setFormOpen(!formOpen)}>
              <h3>Новый код</h3><Icon name="chevdown" size={16} className={'rot' + (formOpen ? ' open' : '')} />
            </button>
            {formOpen && (
              <>
                <div className="group">{<ServiceToggles value={services} onChange={setServices} />}</div>
                <div className="form form--plain">
                  <p className="form__l">Подписка, дней</p>
                  <DayPicker value={days} onChange={setDays} />
                  <div className="row2">
                    <Field label="Использований"><input className="input input--num" type="number" min={1} max={100} inputMode="numeric" value={uses || ''}
                      onChange={(e) => setUses(Math.max(0, Math.min(100, Number(e.target.value) || 0)))} /></Field>
                    <Field label="Заметка"><input className="input" value={note} maxLength={80} placeholder="Для кого" onChange={(e) => setNote(e.target.value)} /></Field>
                  </div>
                  <Button block icon="ticket" disabled={!valid} loading={busy} onClick={() => { haptic.impact('light'); create() }}>Создать код</Button>
                </div>
              </>
            )}
          </section>
        </div>

        <div>
          {loading && !data && <Skeleton rows={3} />}
          {error && !data && <ErrorBox error={error} onRetry={reload} what="Не удалось загрузить коды" />}
          {data && (
            <Section title="Действующие коды">
              {live.length === 0 && (
                <Empty icon="ticket" title="Кодов пока нет — создай первый" text="Код можно отправить человеку прямо из Telegram." />
              )}
              {live.map((c) => <CodeCell key={c.code} c={c} onRevoke={revoke} />)}
            </Section>
          )}
          {dead.length > 0 && (
            <Collapse title="Использованные и истёкшие" count={dead.length}>
              <div className="group">{dead.map((c) => <CodeCell key={c.code} c={c} onRevoke={revoke} />)}</div>
            </Collapse>
          )}
        </div>
      </div>
    </div>
  )
}
