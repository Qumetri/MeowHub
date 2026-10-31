import Icon from '../icons'
import Pass from '../Pass'
import { Avatar, Button, Cell, Pill, Section, toast } from '../ui'
import { t } from '../i18n'
import { haptic, isTg } from '../tg'
import { canDownload, fmtDate, hasService, personName, svcList, uiState } from '../util'
import { useApp } from '../ctx'
import { ContactCell, LangSection, contactVars } from './shared'
import Donate from './Donate'
import type { IconName } from '../icons'
import type { Screen } from '../types'

function ActionBtn({ icon, label, onClick, dim }: { icon: IconName; label: string; onClick: () => void; dim?: boolean }) {
  return (
    <button type="button" className={'abtn' + (dim ? ' dim' : '')} onClick={onClick} aria-disabled={dim || undefined}>
      <span className="abtn__c"><Icon name={icon} size={24} /></span>
      <span className="abtn__l">{label}</span>
    </button>
  )
}

export default function Home() {
  const { me, go } = useApp()
  // Home is the member's root screen: App renders it only for someone with a membership.
  const m = me.member!
  const st = uiState(m)
  const inactive = st === 'expired' || st === 'suspended'
  const hasVpn = hasService(m, 'vpn')
  const hasMx = hasService(m, 'matrix')
  const hasDl = !inactive && canDownload(me)
  const name = personName(m)
  const guard = (ok: boolean, screen: Screen) => () => {
    if (!ok) { haptic.notify('error'); toast(t('toast.noaccess'), 'bad'); return }
    haptic.impact('light'); go(screen)
  }
  const svcName = (id: string) => me.services.find((s) => s.id === id)
  // The catalog (/api/me services) lists only switched-on services; a granted one that is off is hidden.
  const mine = svcList(m).flatMap((id) => {
    const s = svcName(id)
    return s ? [{ id, name: s.name || id, desc: s.description || '' }] : []
  })
  const SVC_ICON: Record<string, IconName> = { vpn: 'shield', matrix: 'chat', tools: 'tools', youtube: 'download', books: 'book', upscale: 'image' }
  const SVC_GO: Record<string, Screen> = { vpn: 'vpn', matrix: 'matrix', youtube: 'downloads', books: 'books', upscale: 'upscale' }
  // "New" pointer to the audiobooks beta until the person has opened it once.
  const hasBooks = !inactive && !!me.services.find((s) => s.id === 'books')?.available
  const booksNew = hasBooks && (() => { try { return !localStorage.getItem('mh_books_seen') } catch { return false } })()
  // Same for photo upscaling.
  const hasUp = !inactive && !!me.services.find((s) => s.id === 'upscale')?.available
  const upNew = hasUp && (() => { try { return !localStorage.getItem('mh_up_seen') } catch { return false } })()

  return (
    <div className="page">
      <Pass member={m} name={name} />

      <div className={'actions' + (hasDl ? ' actions--5' : '')}>
        <ActionBtn icon="shield" label={t('action.vpn')} dim={!hasVpn} onClick={guard(hasVpn, 'vpn')} />
        <ActionBtn icon="chat" label={t('action.messenger')} dim={!hasMx} onClick={guard(hasMx, 'matrix')} />
        {hasDl && <ActionBtn icon="download" label={t('action.downloads')} onClick={() => { haptic.impact('light'); go('downloads') }} />}
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

      {upNew && (
        <Section>
          <Cell icon="image" tone="accent" title={t('home.up_new')} sub={t('home.up_new_sub')} chevron
            onClick={() => { haptic.impact('light'); go('upscale') }} />
        </Section>
      )}

      {booksNew && (
        <Section>
          <Cell icon="book" tone="accent" title={t('home.books_new')} sub={t('home.books_new_sub')} chevron
            onClick={() => { haptic.impact('light'); go('books') }} />
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

      {isTg && !!me.pay?.donate?.length && <Donate amounts={me.pay.donate} />}

      <LangSection />
    </div>
  )
}
