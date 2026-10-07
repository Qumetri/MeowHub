import { useState } from 'react'
import Icon from '../icons.jsx'
import { Cell, Section } from '../ui.jsx'
import { t } from '../i18n.js'
import { haptic } from '../tg.js'
import { useApp } from '../ctx.js'
import { ContactCell, Title, contactHandle, contactVars } from './shared.jsx'

export default function Help() {
  const { me } = useApp()
  const [open, setOpen] = useState(0)
  const vars = contactVars(me)
  return (
    <div className="page">
      <Title>{t('help.title')}</Title>
      {contactHandle(me) && (
        <Section footer={t('help.contact_foot')}>
          <ContactCell me={me} label={t('home.contact', vars)} />
        </Section>
      )}
      <Section title={t('help.faq')}>
        {[1, 2, 3, 4].map((n) => (
          <div key={n} className="faq">
            <Cell title={t('help.q' + n)} onClick={() => { haptic.select(); setOpen(open === n ? 0 : n) }}
              right={<Icon name="chevdown" size={18} className={'cell__chev rot' + (open === n ? ' open' : '')} />} />
            {open === n && <p className="faq__a">{t('help.a' + n, vars)}</p>}
          </div>
        ))}
      </Section>
    </div>
  )
}
