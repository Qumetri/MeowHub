import { useState } from 'react'
import { Cell, ErrorBox, Section, Skeleton, Switch, toast } from '../ui.jsx'
import { api } from '../api.js'
import { useApi } from '../hooks.js'
import { confirmDialog, haptic } from '../tg.js'
import { t } from '../i18n.js'
import { plural } from '../util.js'

const ICON = { vpn: 'shield', matrix: 'chat', tools: 'tools', youtube: 'download' }
const modeNote = (m) => (m === 'all' || m === 'grant' ? t('sv.mode.' + m) : m)
const people = (n) => t('sv.people', { n, unit: plural(n, t('sv.unit_person').split('|')) })

// Owner: master switches for the services members can have (GET/POST /api/admin/services).
export default function Services() {
  const { data, error, loading, reload, setData } = useApi('admin/services')
  const [busy, setBusy] = useState(null)

  if (loading && !data) return <Skeleton rows={4} />
  if (error && !data) return <ErrorBox error={error} onRetry={reload} what={t('sv.load_fail')} />
  const list = Array.isArray(data) ? data : data?.items || []

  async function toggle(s, enabled) {
    if (busy) return
    // Switching a "grant" service off cuts access for everyone who holds it.
    if (!enabled && s.mode === 'grant') {
      const ok = await confirmDialog(t('sv.confirm_off', { name: s.name, n: s.members_with_access }))
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
    <Section title={t('sv.title')} footer={t('sv.foot')}>
      {list.map((s) => (
        <Cell key={s.id} icon={ICON[s.id] || 'info'} tone={s.enabled ? 'accent' : 'mute'} title={s.name} sub={s.description}
          right={<Switch checked={!!s.enabled} disabled={busy === s.id} label={s.name} onChange={(on) => toggle(s, on)} />}
          onClick={() => toggle(s, !s.enabled)}>
          <span className="cell__sub svcmeta">
            <span>{modeNote(s.mode)}</span>
            <span>{people(s.members_with_access ?? 0)}</span>
          </span>
        </Cell>
      ))}
    </Section>
  )
}
