import { useState } from 'react'
import Icon from '../icons.jsx'
import { Cell, Chips, Pill, Switch } from '../ui.jsx'
import { t } from '../i18n.js'
import { statusLabel, uiState } from '../util.js'

export const SERVICE_IDS = ['vpn', 'matrix', 'tools']
const SVC_ICONS = { vpn: 'shield', matrix: 'chat', tools: 'tools' }
// Names/descriptions live in the dictionary (svc.<id>.name / .desc) so they follow the language.
export const svcMeta = (id) => ({ icon: SVC_ICONS[id], name: t(`svc.${id}.name`), desc: t(`svc.${id}.desc`) })
export const svcName = (id) => (SVC_ICONS[id] ? t(`svc.${id}.name`) : id)

export function ServiceToggles({ value, onChange, disabled }) {
  const set = (id, on) => onChange(SERVICE_IDS.filter((s) => (s === id ? on : value.includes(s))))
  return SERVICE_IDS.map((id) => {
    const sv = svcMeta(id)
    return (
      <Cell key={id} icon={sv.icon} title={sv.name} sub={sv.desc}
        right={<Switch checked={value.includes(id)} disabled={disabled} label={sv.name} onChange={(on) => set(id, on)} />}
        onClick={disabled ? undefined : () => set(id, !value.includes(id))} />
    )
  })
}

export function ServiceIcons({ services }) {
  return (
    <span className="svcicons">
      {SERVICE_IDS.map((id) => (
        <span key={id} className={services.includes(id) ? 'on' : ''} title={svcName(id)}>
          <Icon name={SVC_ICONS[id]} size={15} />
        </span>
      ))}
    </span>
  )
}

export const PILL_TONE = { active: 'ok', soon: 'warn', expired: 'bad', suspended: 'mute' }
export function StatusPill({ m }) {
  return <Pill tone={PILL_TONE[uiState(m)]}>{statusLabel(m)}</Pill>
}

export function leftLabel(m) {
  if (m.expires_ts == null) return '∞'
  if (m.status !== 'active') return '—'
  return t('left.days', { n: m.days_left })
}

// 30 / 90 / 180 / 365 chips plus a custom number.
export function DayPicker({ value, onChange, presets = [30, 90, 180, 365], min = 1 }) {
  const [custom, setCustom] = useState(!presets.includes(value))
  const items = [...presets.map((d) => ({ id: String(d), label: String(d) })), { id: 'custom', label: '…' }]
  return (
    <div className="daypick">
      <Chips items={items} value={custom ? 'custom' : String(value)}
        onChange={(id) => { if (id === 'custom') setCustom(true); else { setCustom(false); onChange(Number(id)) } }} />
      {custom && (
        <input className="input input--num" type="number" inputMode="numeric" min={min} max={3650} value={value || ''}
          aria-label={t('dp.days')} placeholder={t('dp.days')}
          onChange={(e) => onChange(Math.max(0, Math.min(3650, Number(e.target.value) || 0)))} />
      )}
    </div>
  )
}
