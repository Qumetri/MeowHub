import { useState } from 'react'
import Icon from '../icons'
import { Button, Cell, Collapse, Empty, ErrorBox, Field, Pill, Section, Skeleton, doCopy, toast } from '../ui'
import { api } from '../api'
import { t } from '../i18n'
import { useApi } from '../hooks'
import { confirmDialog, haptic, isTg, openTelegramLink } from '../tg'
import { daysWord, fmtDate } from '../util'
import { useApp } from '../ctx'
import { DayPicker, ServiceToggles, svcName } from './shared'
import type { AccessCode, NewCode, Tone } from '../types'

function share(url: string): void {
  if (isTg) openTelegramLink('https://t.me/share/url?url=' + encodeURIComponent(url))
  else void doCopy(url)
}

function Fresh({ fresh, onClose }: { fresh: NewCode; onClose: () => void }) {
  return (
    <div className="fresh" role="status">
      <p className="fresh__l">{t('cd.fresh')}</p>
      <p className="fresh__code">{fresh.code}</p>
      <div className="fresh__b">
        <Button kind="tonal" icon="copy" onClick={() => { haptic.impact('light'); doCopy(fresh.code) }}>{t('common.copy')}</Button>
        <Button icon={isTg ? 'send' : 'link'} onClick={() => { haptic.impact('light'); share(fresh.share_url) }}>
          {isTg ? t('common.share') : t('cd.copy_link')}
        </Button>
        <Button kind="plain" onClick={onClose}>{t('common.hide')}</Button>
      </div>
    </div>
  )
}

function CodeCell({ c, onRevoke }: { c: AccessCode; onRevoke: (c: AccessCode) => void }) {
  const names = (c.services || []).map(svcName).join(', ')
  const live = c.state === 'live'
  const STATE_TONE: Record<AccessCode['state'], Tone> = { live: 'accent', used: 'mute', expired: 'warn', revoked: 'bad' }
  const stateTone = STATE_TONE[c.state]
  const stateText = c.state !== 'live' ? t('cd.st.' + c.state) : ''
  return (
    <Cell icon="ticket" tone={live ? 'accent' : 'mute'} title={<span className="mono">{c.code}</span>}
      sub={t('cd.sub', { days: c.days, unit: daysWord(c.days), names, uses: c.uses, max: c.uses_max }) + (c.note ? ' · ' + c.note : '')}
      right={
        <span className="cell__r">
          {live
            ? (
              <>
                <button type="button" className="iconbtn" aria-label={t('cd.copy_link_aria')} onClick={() => { haptic.impact('light'); share(c.share_url) }}>
                  <Icon name={isTg ? 'send' : 'link'} size={18} />
                </button>
                <button type="button" className="iconbtn iconbtn--danger" aria-label={t('cd.revoke_aria')} onClick={() => onRevoke(c)}>
                  <Icon name="trash" size={18} />
                </button>
              </>
            )
            : <Pill tone={stateTone}>{stateText}</Pill>}
        </span>
      }>
      {live && <span className="cell__sub">{t('cd.valid_until', { date: fmtDate(c.valid_until, true) })}</span>}
    </Cell>
  )
}

export default function Codes() {
  const { version, bump } = useApp()
  const { data, error, loading, reload } = useApi<AccessCode[]>('admin/codes', [version])
  const [services, setServices] = useState(['vpn', 'matrix'])
  const [days, setDays] = useState(30)
  const [uses, setUses] = useState(1)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [fresh, setFresh] = useState<NewCode | null>(null)
  const [formOpen, setFormOpen] = useState(true)

  async function create() {
    setBusy(true)
    try {
      const r = await api<NewCode>('admin/codes', { method: 'POST', body: { days, services, uses_max: uses, note } })
      haptic.notify('success'); setFresh(r); setNote(''); bump(); void reload()
    } catch (e) { haptic.notify('error'); toast((e as Error).message, 'bad') } finally { setBusy(false) }
  }
  async function revoke(c: AccessCode) {
    if (!(await confirmDialog(t('cd.revoke_q', { code: c.code })))) return
    try {
      await api(`admin/codes/${encodeURIComponent(c.code)}/revoke`, { method: 'POST' })
      haptic.notify('success'); toast(t('cd.revoked_toast')); void reload()
    } catch (e) { haptic.notify('error'); toast((e as Error).message, 'bad') }
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
              <h3>{t('cd.new_title')}</h3><Icon name="chevdown" size={16} className={'rot' + (formOpen ? ' open' : '')} />
            </button>
            {formOpen && (
              <>
                <div className="group">{<ServiceToggles value={services} onChange={setServices} />}</div>
                <div className="form form--plain">
                  <p className="form__l">{t('cd.days')}</p>
                  <DayPicker value={days} onChange={setDays} />
                  <div className="row2">
                    <Field label={t('cd.uses')}><input className="input input--num" type="number" min={1} max={100} inputMode="numeric" value={uses || ''}
                      onChange={(e) => setUses(Math.max(0, Math.min(100, Number(e.target.value) || 0)))} /></Field>
                    <Field label={t('cd.note')}><input className="input" value={note} maxLength={80} placeholder={t('cd.note_ph')} onChange={(e) => setNote(e.target.value)} /></Field>
                  </div>
                  <Button block icon="ticket" disabled={!valid} loading={busy} onClick={() => { haptic.impact('light'); void create() }}>{t('cd.create')}</Button>
                </div>
              </>
            )}
          </section>
        </div>

        <div>
          {loading && !data && <Skeleton rows={3} />}
          {error && !data && <ErrorBox error={error} onRetry={reload} what={t('cd.load_fail')} />}
          {data && (
            <Section title={t('cd.live')}>
              {live.length === 0 && (
                <Empty icon="ticket" title={t('cd.empty_t')} text={t('cd.empty_x')} />
              )}
              {live.map((c) => <CodeCell key={c.code} c={c} onRevoke={revoke} />)}
            </Section>
          )}
          {dead.length > 0 && (
            <Collapse title={t('cd.dead')} count={dead.length}>
              <div className="group">{dead.map((c) => <CodeCell key={c.code} c={c} onRevoke={revoke} />)}</div>
            </Collapse>
          )}
        </div>
      </div>
    </div>
  )
}
