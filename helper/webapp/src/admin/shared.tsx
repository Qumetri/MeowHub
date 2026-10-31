import { useState } from 'react'
import Icon from '../icons'
import { Cell, Chips, Pill, Switch } from '../ui'
import { t } from '../i18n'
import { statusLabel, uiState } from '../util'
import type { IconName } from '../icons'
import type { Member, Tone, UiState } from '../types'

export const SERVICE_IDS = ['vpn', 'matrix', 'tools']
const SVC_ICONS: Record<string, IconName> = { vpn: 'shield', matrix: 'chat', tools: 'tools' }
// Names/descriptions live in the dictionary (svc.<id>.name / .desc) so they follow the language.
export const svcMeta = (id: string): { icon: IconName | undefined; name: string; desc: string } => ({ icon: SVC_ICONS[id], name: t(`svc.${id}.name`), desc: t(`svc.${id}.desc`) })
export const svcName = (id: string): string => (SVC_ICONS[id] ? t(`svc.${id}.name`) : id)

export function ServiceToggles({ value, onChange, disabled }: { value: string[]; onChange: (v: string[]) => void; disabled?: boolean }) {
  const set = (id: string, on: boolean) => onChange(SERVICE_IDS.filter((s) => (s === id ? on : value.includes(s))))
  return SERVICE_IDS.map((id) => {
    const sv = svcMeta(id)
    return (
      <Cell key={id} icon={sv.icon} title={sv.name} sub={sv.desc}
        right={<Switch checked={value.includes(id)} disabled={disabled} label={sv.name} onChange={(on) => set(id, on)} />}
        onClick={disabled ? undefined : () => set(id, !value.includes(id))} />
    )
  })
}

export function ServiceIcons({ services }: { services: string[] }) {
  return (
    <span className="svcicons">
      {SERVICE_IDS.map((id) => (
        <span key={id} className={services.includes(id) ? 'on' : ''} title={svcName(id)}>
          <Icon name={SVC_ICONS[id] ?? 'info'} size={15} />
        </span>
      ))}
    </span>
  )
}

export const PILL_TONE: Record<UiState, Tone> = { active: 'ok', soon: 'warn', expired: 'bad', suspended: 'mute' }
export function StatusPill({ m }: { m: Member }) {
  return <Pill tone={PILL_TONE[uiState(m)]}>{statusLabel(m)}</Pill>
}

export function leftLabel(m: Member): string {
  if (m.expires_ts == null) return '∞'
  if (m.status !== 'active') return '—'
  return t('left.days', { n: m.days_left })
}

// 30 / 90 / 180 / 365 chips plus a custom number.
export function DayPicker({ value, onChange, presets = [30, 90, 180, 365], min = 1 }: { value: number; onChange: (days: number) => void; presets?: number[]; min?: number }) {
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
