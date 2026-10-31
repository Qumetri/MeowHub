import { useState } from 'react'
import Icon from '../icons'
import { Cell, Section } from '../ui'
import { t } from '../i18n'
import { haptic, isTg } from '../tg'
import { useApp } from '../ctx'
import { ContactCell, Title, contactHandle, contactVars } from './shared'
import Donate from './Donate'

export default function Help() {
  const { me } = useApp()
  const [open, setOpen] = useState<number>(0)
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
      {isTg && !!me.pay?.donate?.length && <Donate amounts={me.pay.donate} />}
    </div>
  )
}
