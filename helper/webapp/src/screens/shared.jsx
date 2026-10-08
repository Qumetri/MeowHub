import { useState } from 'react'
import Icon from '../icons.jsx'
import { Button, Cell, Section, Segmented } from '../ui.jsx'
import { t } from '../i18n.js'
import { haptic, isTg, openTelegramLink } from '../tg.js'
import { useApp } from '../ctx.js'

// Page title; in browser mode also a back link (Telegram has a native button).
export function Title({ children, sub }) {
  const { back, depth } = useApp()
  return (
    <header className="title">
      {!isTg && depth > 0 && (
        <button type="button" className="backlink" onClick={back}><Icon name="chevleft" size={16} />{t('common.back')}</button>
      )}
      <h1>{children}</h1>
      {sub && <p>{sub}</p>}
    </header>
  )
}

// The owner's Telegram handle from /api/me (OWNER_CONTACT on the server), or ''
// when none is configured -- nothing here is hardcoded.
export function contactHandle(me) {
  const c = (me?.contact || '').trim()
  if (!c) return ''
  return c.startsWith('@') ? c : '@' + c
}

// Substitution values for strings that mention the contact; they fall back to
// "the owner" in the user's language when no handle is configured.
export function contactVars(me) {
  const h = contactHandle(me)
  return { contact: h || t('contact.owner'), contact_from: h || t('contact.owner_from') }
}

export function ContactCell({ me, label }) {
  const h = contactHandle(me)
  if (!h) return null
  return (
    <Cell icon="send" title={label || t('stranger.nocode', contactVars(me))} chevron
      onClick={() => { haptic.impact('light'); openTelegramLink('https://t.me/' + h.slice(1)) }} />
  )
}

const REDEEM_ERR = ['invalid', 'used', 'expired_code', 'revoked_code', 'rate_limited', 'suspended']
export function redeemMessage(result, me, message) {
  if (REDEEM_ERR.includes(result)) return t('redeem.' + result, contactVars(me))
  return message || t('err.generic')
}

// "Язык / Language": Auto · Русский · English. Writes the server-side preference (App.changeLang).
export function LangSection() {
  const { langPref, setLangPref } = useApp()
  const items = ['auto', 'ru', 'en'].map((id) => ({ id, label: t('lang.' + id) }))
  return (
    <Section title={t('lang.title')} footer={t('lang.hint')}>
      <div className="langrow"><Segmented items={items} value={langPref} onChange={setLangPref} /></div>
    </Section>
  )
}

export function useShow() {
  const [shown, setShown] = useState(false)
  return [shown, () => setShown((s) => !s)]
}

export { Button }
