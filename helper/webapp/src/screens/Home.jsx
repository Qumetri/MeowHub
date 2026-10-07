import Icon from '../icons.jsx'
import Pass from '../Pass.jsx'
import { Avatar, Button, Cell, Pill, Section, toast } from '../ui.jsx'
import { t } from '../i18n.js'
import { haptic } from '../tg.js'
import { fmtDate, hasService, personName, uiState } from '../util.js'
import { useApp } from '../ctx.js'
import { ContactCell, contactVars } from './shared.jsx'

function ActionBtn({ icon, label, onClick, dim }) {
  return (
    <button type="button" className={'abtn' + (dim ? ' dim' : '')} onClick={onClick} aria-disabled={dim || undefined}>
      <span className="abtn__c"><Icon name={icon} size={24} /></span>
      <span className="abtn__l">{label}</span>
    </button>
  )
}

export default function Home() {
  const { me, go } = useApp()
  const m = me.member
  const st = uiState(m)
  const inactive = st === 'expired' || st === 'suspended'
  const hasVpn = hasService(m, 'vpn')
  const hasMx = hasService(m, 'matrix')
  const name = personName(m)
  const guard = (ok, screen) => () => {
    if (!ok) { haptic.notify('error'); toast(t('toast.noaccess'), 'bad'); return }
    haptic.impact('light'); go(screen)
  }
  const svcName = (id) => me.services.find((s) => s.id === id)
  const mine = (m.services || []).map((id) => {
    const s = svcName(id)
    return { id, name: s?.name || id, desc: s?.description || '' }
  })
  const SVC_ICON = { vpn: 'shield', matrix: 'chat', tools: 'tools' }
  const SVC_GO = { vpn: 'vpn', matrix: 'matrix' }

  return (
    <div className="page">
      <Pass member={m} name={name} />

      <div className="actions">
        <ActionBtn icon="shield" label={t('action.vpn')} dim={!hasVpn} onClick={guard(hasVpn, 'vpn')} />
        <ActionBtn icon="chat" label={t('action.messenger')} dim={!hasMx} onClick={guard(hasMx, 'matrix')} />
        <ActionBtn icon="calplus" label={t('action.extend')} onClick={() => { haptic.impact('light'); go('code') }} />
        <ActionBtn icon="help" label={t('action.help')} onClick={() => { haptic.impact('light'); go('help') }} />
      </div>

      {inactive && (
        <Section footer={t('home.kept')}>
          <div className="cta">
            <Button kind="primary" icon="key" block onClick={() => { haptic.impact('light'); go('code') }}>{t('home.enter_code')}</Button>
          </div>
          <ContactCell me={me} label={t('home.contact', contactVars(me))} />
        </Section>
      )}

      <Section title={t('home.services')}>
        {mine.length === 0 && <Cell icon="info" tone="mute" title="—" />}
        {mine.map((s) => {
          const on = !inactive
          const target = SVC_GO[s.id]
          return (
            <Cell key={s.id} icon={SVC_ICON[s.id] || 'info'} tone={on ? 'accent' : 'mute'} title={s.name}
              sub={s.id === 'tools' && !s.desc ? t('svc.tools_desc') : s.desc}
              right={<Pill tone={on ? 'ok' : 'mute'}>{on ? t('home.working') : t('home.paused')}</Pill>}
              chevron={on && !!target}
              onClick={on && target ? () => { haptic.impact('light'); go(target) } : undefined} />
          )
        })}
      </Section>

      <Section title={t('home.profile')}>
        <Cell lead={<Avatar uid={m.id} member={m} size={30} />} title={name}
          sub={m.username ? '@' + m.username : undefined} />
        <Cell icon="calendar" tone="mute" title={t('home.since')} value={fmtDate(m.created_ts, true)} />
      </Section>
    </div>
  )
}
