import { useState } from 'react'
import { Button, Cell, ErrorBox, Pill, Section, Skeleton } from '../ui.jsx'
import Icon from '../icons.jsx'
import { useApi } from '../hooks.js'
import { fmtBytes, fmtDateTime, plural } from '../util.js'
import { useApp } from '../ctx.js'

const ST = {
  queued: ['mute', 'В очереди'], running: ['accent', 'Скачивается'], done: ['ok', 'Готово'],
  error: ['bad', 'Ошибка'], canceled: ['mute', 'Отменено'], expired: ['mute', 'Удалён'],
}
const who = (j) => j.user_name || j.user?.name || (typeof j.user === 'string' ? j.user : '') || (j.uid != null ? String(j.uid) : '—')
const SHOWN = 8

// Owner, read-only: disk, active count, today's totals and the recent jobs (GET /api/admin/dl).
export default function DlAdmin() {
  const { version } = useApp()
  const { data, error, loading, reload } = useApi('admin/dl', [version])
  const [all, setAll] = useState(false)

  if (loading && !data) return <Skeleton rows={3} />
  if (error && !data) return <ErrorBox error={error} onRetry={reload} what="Не удалось загрузить загрузки" />

  const jobs = data.jobs || data.items || []
  const shown = all ? jobs : jobs.slice(0, SHOWN)
  const free = data.disk_free_gb
  const low = free != null && free < 50
  const t = data.today || {}
  return (
    <>
      <Section title="Загрузки" footer={low ? 'Меньше 50 ГБ: новые загрузки отклоняются.' : undefined}>
        <Cell icon="server" tone={low ? 'warn' : 'accent'} title="Свободно на диске"
          value={free != null ? fmtBytes(free * 1024 ** 3) : '—'} />
        <Cell icon="download" tone={data.active ? 'ok' : 'mute'} title="Сейчас качается" value={data.active ?? 0} />
        <Cell icon="calendar" tone="mute" title="За сегодня"
          value={`${t.jobs ?? 0} ${plural(t.jobs ?? 0, ['загрузка', 'загрузки', 'загрузок'])}, ${fmtBytes(t.bytes ?? 0)}`} />
      </Section>
      <section className="sec">
        <div className="sec__h"><h3>Последние за 24 часа{jobs.length ? <em> {jobs.length}</em> : null}</h3></div>
        <div className="group">
          {jobs.length === 0 && <Cell icon="info" tone="mute" title="Пока ничего не качали" />}
          {shown.map((j) => {
            const [tone, label] = ST[j.status] || ['mute', j.status]
            return (
              <div className="dlrow" key={j.id}>
                <div className="dlrow__main">
                  <b>{j.title || j.url}</b>
                  <span>{who(j)}{j.preset_label ? ` · ${j.preset_label}` : ''}{j.created_ts ? ` · ${fmtDateTime(j.created_ts)}` : ''}</span>
                  {j.status === 'error' && j.error && <span className="bad">{j.error}</span>}
                </div>
                <div className="dlrow__r">
                  <Pill tone={tone}>{label}</Pill>
                  {j.size != null && <small>{fmtBytes(j.size)}</small>}
                </div>
              </div>
            )
          })}
        </div>
        {jobs.length > SHOWN && (
          <div className="more">
            <Button kind="plain" size="s" onClick={() => setAll(!all)}>
              {all ? 'Свернуть' : `Показать все (${jobs.length})`}
              <Icon name="chevdown" size={16} className={'rot' + (all ? ' open' : '')} />
            </Button>
          </div>
        )}
      </section>
    </>
  )
}
