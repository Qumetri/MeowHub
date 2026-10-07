import { useState } from 'react'
import { Cell, ErrorBox, Section, Skeleton, Switch, toast } from '../ui.jsx'
import { api } from '../api.js'
import { useApi } from '../hooks.js'
import { confirmDialog, haptic } from '../tg.js'
import { plural } from '../util.js'

const ICON = { vpn: 'shield', matrix: 'chat', tools: 'tools', youtube: 'download' }
const MODE_NOTE = { all: 'для всех активных участников', grant: 'главный выключатель' }
const people = (n) => `${n} ${plural(n, ['участник', 'участника', 'участников'])} с доступом`

// Owner: master switches for the services members can have (GET/POST /api/admin/services).
export default function Services() {
  const { data, error, loading, reload, setData } = useApi('admin/services')
  const [busy, setBusy] = useState(null)

  if (loading && !data) return <Skeleton rows={4} />
  if (error && !data) return <ErrorBox error={error} onRetry={reload} what="Не удалось загрузить сервисы" />
  const list = Array.isArray(data) ? data : data?.items || []

  async function toggle(s, enabled) {
    if (busy) return
    // Switching a "grant" service off cuts access for everyone who holds it.
    if (!enabled && s.mode === 'grant') {
      const ok = await confirmDialog(`Выключить «${s.name}»? Доступ пропадёт у всех участников (${s.members_with_access}), пока снова не включишь.`)
      if (!ok) return
    }
    const prev = list
    setBusy(s.id)
    setData(list.map((x) => (x.id === s.id ? { ...x, enabled } : x))) // optimistic
    try {
      const next = await api('admin/services/' + s.id, { method: 'POST', body: { enabled } })
      setData(Array.isArray(next) ? next : next?.items || prev)
      haptic.notify('success')
    } catch (e) {
      setData(prev)
      haptic.notify('error'); toast(e.message, 'bad')
    } finally { setBusy(null) }
  }

  return (
    <Section title="Сервисы для участников" footer="Выключенный сервис исчезает у всех участников и в приложении, и в боте.">
      {list.map((s) => (
        <Cell key={s.id} icon={ICON[s.id] || 'info'} tone={s.enabled ? 'accent' : 'mute'} title={s.name} sub={s.description}
          right={<Switch checked={!!s.enabled} disabled={busy === s.id} label={s.name} onChange={(on) => toggle(s, on)} />}
          onClick={() => toggle(s, !s.enabled)}>
          <span className="cell__sub svcmeta">
            <span>{MODE_NOTE[s.mode] || s.mode}</span>
            <span>{people(s.members_with_access ?? 0)}</span>
          </span>
        </Cell>
      ))}
    </Section>
  )
}
