import { useEffect, useRef, useState } from 'react'
import Icon from '../icons.jsx'
import { Button, Dot, ErrorBox, Field, Sheet, Skeleton, toast } from '../ui.jsx'
import { api, relUrl } from '../api.js'
import { useApi } from '../hooks.js'
import { confirmDialog, haptic } from '../tg.js'

// "Боты и ключи": the four secrets (3 bots + the 3x-ui API token), their state,
// and a safe way to swap each one. Tokens never come back from the server in
// full - only the masked form - and are only ever typed into the form below.
const META = {
  helper: { role: 'Служебный бот', icon: 'bot' },
  member: { role: 'Бот для участников', icon: 'users' },
  crypto: { role: 'Крипто-бот', icon: 'bot' },
  xui: { role: '3x-ui API', icon: 'server' },
}
const SOURCE = {
  env: ['.env', 'env'],
  page: ['задан здесь', 'page'],
  ctl: ['через ctl', 'env'],
  none: ['не задан', 'none'],
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

function BotAvatar({ item }) {
  const [bad, setBad] = useState(false)
  const b = item.bot
  const src = item.kind === 'bot' && b?.id ? (b.avatar_url ? relUrl(b.avatar_url) : './api/admin/bot-avatar/' + b.id) : null
  return (
    <span className="keyav" aria-hidden="true">
      <Icon name={META[item.id]?.icon || 'bot'} size={22} />
      {src && !bad && <img src={src} alt="" onError={() => setBad(true)} />}
    </span>
  )
}

function ShowToggle({ show, onToggle }) {
  return (
    <button type="button" className="iconbtn iconbtn--in" onClick={onToggle} aria-label={show ? 'Скрыть' : 'Показать'}>
      <Icon name={show ? 'eyeoff' : 'eye'} size={18} />
    </button>
  )
}

// Paste field + "check and save". Used inline (unconfigured member bot) and inside the sheet.
function KeyForm({ item, onSaved, autoFocus }) {
  const [token, setToken] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const ready = token.trim().length > 0
  const isApi = item.kind === 'api'

  async function submit(e) {
    e?.preventDefault()
    if (!ready || busy) return
    haptic.impact('light')
    setBusy(true); setErr('')
    try {
      const r = await api('admin/integrations/' + item.id, { method: 'POST', body: { token: token.trim() } })
      haptic.notify('success'); setToken('')
      onSaved(r)
    } catch (ex) { haptic.notify('error'); setErr(ex.message) } finally { setBusy(false) }
  }
  return (
    <form className="keyform" onSubmit={submit} autoComplete="off">
      <Field label={isApi ? 'Новый API-токен' : 'Токен бота'} error={err}
        suffix={<ShowToggle show={show} onToggle={() => setShow(!show)} />}>
        <input className="input mono" type={show ? 'text' : 'password'} value={token} autoFocus={autoFocus}
          placeholder={isApi ? 'Вставь токен' : '123456789:AA…'} aria-label={isApi ? 'API-токен' : 'Токен бота'}
          name={'k-' + item.id} autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false} data-lpignore="true"
          onChange={(e) => { setToken(e.target.value); setErr('') }} />
      </Field>
      {item.note && <p className="keynote"><Icon name="warning" size={16} />{item.note}</p>}
      <Button type="submit" block loading={busy} disabled={!ready}>Проверить и сохранить</Button>
    </form>
  )
}

function KeyCard({ item, busy, restarting, onChange, onReset }) {
  const meta = META[item.id] || { role: item.id }
  const [srcLabel, srcKind] = SOURCE[item.source] || SOURCE.none
  const b = item.bot
  const chk = item.check
  const ok = item.kind === 'api' ? chk?.ok : b?.ok
  const err = item.kind === 'api' ? chk?.error : b?.error
  const guide = item.id === 'member' && !item.configured

  let title = b?.name || meta.role
  let sub = b?.username ? '@' + b.username : ''
  if (item.kind === 'api') { title = 'API-токен панели'; sub = chk?.ok ? chk.detail : '' }
  if (guide) { title = 'Не подключён'; sub = '' }

  return (
    <article className={'keycard' + (restarting ? ' is-busy' : '')} aria-busy={restarting || undefined}>
      <div className="keycard__top">
        <BotAvatar item={item} />
        <span className="keycard__id">
          <span className="keycard__role">{meta.role}</span>
          <b>{title}</b>
          {sub && <span className="keycard__sub">{sub}</span>}
        </span>
        {item.configured && ok != null && (
          <span className={'botstate ' + (ok ? 'ok' : 'bad')}><Dot on={ok} />{ok ? (item.kind === 'api' ? 'доступен' : 'работает') : 'ошибка'}</span>
        )}
      </div>

      {guide ? (
        <>
          <ol className="keysteps">
            <li><span>В <b>@BotFather</b>: <code>/newbot</code> → имя → username.</span></li>
            <li><span>Скопируй токен.</span></li>
            <li><span>Вставь сюда.</span></li>
          </ol>
          <p className="keywhy">Участники будут пользоваться отдельным ботом, а этот останется твоим служебным.</p>
          <KeyForm item={item} onSaved={onChange} />
        </>
      ) : (
        <>
          <div className="keycard__tok">
            <span className={'srcchip srcchip--' + srcKind}>{srcLabel}</span>
            {item.masked && <code className="mono keycard__mask">{item.masked}</code>}
          </div>
          {err && <p className="keycard__err" role="alert">{err}</p>}
          {item.note && <p className="keynote"><Icon name="warning" size={16} />{item.note}</p>}
          {restarting ? (
            <p className="keycard__restart" role="status"><span className="spin" />Бот перезапускается…</p>
          ) : (
            <div className="keycard__act">
              <Button kind="tonal" size="s" icon="key" disabled={busy} onClick={() => onChange(null)}>Сменить ключ</Button>
              {item.source === 'page' && <Button kind="plain" size="s" disabled={busy} onClick={onReset}>Вернуть из .env</Button>}
            </div>
          )}
        </>
      )}
    </article>
  )
}

export default function Integrations() {
  const { data, error, loading, reload, setData } = useApi('admin/integrations')
  const [editing, setEditing] = useState(null) // item id whose key sheet is open
  const [restarting, setRestarting] = useState(null)
  const [busy, setBusy] = useState(false)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const items = data?.items || []

  // A bot restart drops the API for a moment: poll every 2 s for up to 30 s until it answers again.
  async function waitBack(id) {
    setRestarting(id)
    const t0 = Date.now()
    let res = null
    while (alive.current && Date.now() - t0 < 30000) {
      await sleep(2000)
      try { res = await api('admin/integrations'); break } catch (e) {
        if (e.status === 401) { toast('Ключ сменился: открой приложение из бота заново', 'bad'); break }
      }
    }
    if (!alive.current) return
    setRestarting(null)
    if (res) { setData(res); toast('Бот перезапущен') } else {
      toast('Бот не ответил за 30 секунд — проверь логи', 'bad')
      reload()
    }
  }

  async function saved(id, r) {
    setEditing(null)
    setData({ items: items.map((x) => (x.id === id ? r.item : x)) })
    toast('Ключ сохранён')
    if (r.restarting) await waitBack(id); else reload()
  }
  async function reset(item) {
    if (!(await confirmDialog(`Вернуть ключ из .env? Ключ, заданный здесь, будет удалён (${META[item.id]?.role}).`))) return
    setBusy(true)
    try {
      const r = await api(`admin/integrations/${item.id}/reset`, { method: 'POST' })
      haptic.notify('success')
      setData({ items: items.map((x) => (x.id === item.id ? r.item : x)) })
      toast('Возвращено из .env')
      if (r.restarting) await waitBack(item.id); else reload()
    } catch (e) { haptic.notify('error'); toast(e.message, 'bad') } finally { setBusy(false) }
  }

  const edit = items.find((x) => x.id === editing)
  return (
    <section className="sec">
      <div className="sec__h"><h3>Боты и ключи</h3></div>
      {loading && !data && <Skeleton rows={3} title={false} />}
      {error && !data && <ErrorBox error={error} onRetry={reload} what="Не удалось загрузить ключи" />}
      <div className="keys">
        {items.map((it) => (
          <KeyCard key={it.id} item={it} busy={busy || !!restarting} restarting={restarting === it.id}
            onChange={(r) => (r ? saved(it.id, r) : setEditing(it.id))} onReset={() => reset(it)} />
        ))}
      </div>
      <Sheet open={!!edit} title="Сменить ключ" onClose={() => setEditing(null)}>
        {edit && (
          <>
            <p className="sheet__sub">{META[edit.id]?.role}{edit.bot?.username ? ` · @${edit.bot.username}` : ''}</p>
            <KeyForm item={edit} autoFocus onSaved={(r) => saved(edit.id, r)} />
          </>
        )}
      </Sheet>
    </section>
  )
}
