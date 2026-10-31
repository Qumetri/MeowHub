import { useEffect, useRef, useState } from 'react'
import Icon from '../icons'
import { Button, Dot, ErrorBox, Field, Sheet, Skeleton, toast, useAuthedImage } from '../ui'
import { api, relUrl } from '../api'
import { t } from '../i18n'
import { useApi } from '../hooks'
import { confirmDialog, haptic } from '../tg'
import type { FormEvent } from 'react'
import type { ApiError } from '../api'
import type { IconName } from '../icons'
import type { KeyItem, KeySaved } from '../types'

// "Боты и ключи": the four secrets (3 bots + the 3x-ui API token), their state,
// and a safe way to swap each one. Tokens never come back from the server in
// full - only the masked form - and are only ever typed into the form below.
const META: Record<string, { icon: IconName }> = {
  helper: { icon: 'bot' },
  member: { icon: 'users' },
  crypto: { icon: 'bot' },
  xui: { icon: 'server' },
}
const role = (id: string): string => (META[id] ? t('ig.role.' + id) : id)
const SOURCE_KIND: Record<KeyItem['source'], 'env' | 'page' | 'none'> = { env: 'env', page: 'page', ctl: 'env', none: 'none' }
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms))

function BotAvatar({ item }: { item: KeyItem }) {
  const [bad, setBad] = useState(false)
  const b = item.bot
  const raw = item.kind === 'bot' && b?.id ? (b.avatar_url ? relUrl(b.avatar_url) : './api/admin/bot-avatar/' + b.id) : null
  const img = useAuthedImage(raw)
  const src = img.bad ? null : img.src
  return (
    <span className="keyav" aria-hidden="true">
      <Icon name={META[item.id]?.icon || 'bot'} size={22} />
      {src && !bad && <img src={src} alt="" onError={() => setBad(true)} />}
    </span>
  )
}

function ShowToggle({ show, onToggle }: { show: boolean; onToggle: () => void }) {
  return (
    <button type="button" className="iconbtn iconbtn--in" onClick={onToggle} aria-label={show ? t('common.hide') : t('common.show')}>
      <Icon name={show ? 'eyeoff' : 'eye'} size={18} />
    </button>
  )
}

// Paste field + "check and save". Used inline (unconfigured member bot) and inside the sheet.
function KeyForm({ item, onSaved, autoFocus }: { item: KeyItem; onSaved: (r: KeySaved) => void; autoFocus?: boolean }) {
  const [token, setToken] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const ready = token.trim().length > 0
  const isApi = item.kind === 'api'

  async function submit(e?: FormEvent) {
    e?.preventDefault()
    if (!ready || busy) return
    haptic.impact('light')
    setBusy(true); setErr('')
    try {
      const r = await api<KeySaved>('admin/integrations/' + item.id, { method: 'POST', body: { token: token.trim() } })
      haptic.notify('success'); setToken('')
      onSaved(r)
    } catch (ex) { haptic.notify('error'); setErr((ex as Error).message) } finally { setBusy(false) }
  }
  return (
    <form className="keyform" onSubmit={(e) => { void submit(e) }} autoComplete="off">
      <Field label={isApi ? t('ig.new_api') : t('ig.bot_token')} error={err}
        suffix={<ShowToggle show={show} onToggle={() => setShow(!show)} />}>
        <input className="input mono" type={show ? 'text' : 'password'} value={token} autoFocus={autoFocus}
          placeholder={isApi ? t('ig.paste') : '123456789:AA…'} aria-label={isApi ? t('ig.api_token_aria') : t('ig.bot_token')}
          name={'k-' + item.id} autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false} data-lpignore="true"
          onChange={(e) => { setToken(e.target.value); setErr('') }} />
      </Field>
      {item.note && <p className="keynote"><Icon name="warning" size={16} />{item.note}</p>}
      <Button type="submit" block loading={busy} disabled={!ready}>{t('ig.check_save')}</Button>
    </form>
  )
}

interface KeyCardProps { item: KeyItem; busy: boolean; restarting: boolean; onChange: (r: KeySaved | null) => void; onReset: () => void }
function KeyCard({ item, busy, restarting, onChange, onReset }: KeyCardProps) {
  const srcKind = SOURCE_KIND[item.source] || 'none'
  const srcLabel = t('ig.src.' + (item.source in SOURCE_KIND ? item.source : 'none'))
  const b = item.bot
  const chk = item.check
  const ok = item.kind === 'api' ? chk?.ok : b?.ok
  const err = item.kind === 'api' ? chk?.error : b?.error
  const guide = item.id === 'member' && !item.configured

  let title = b?.name || role(item.id)
  let sub = b?.username ? '@' + b.username : ''
  if (item.kind === 'api') { title = t('ig.panel_token'); sub = chk?.ok ? chk.detail ?? '' : '' }
  if (guide) { title = t('ig.not_connected'); sub = '' }

  return (
    <article className={'keycard' + (restarting ? ' is-busy' : '')} aria-busy={restarting || undefined}>
      <div className="keycard__top">
        <BotAvatar item={item} />
        <span className="keycard__id">
          <span className="keycard__role">{role(item.id)}</span>
          <b>{title}</b>
          {sub && <span className="keycard__sub">{sub}</span>}
        </span>
        {item.configured && ok != null && (
          <span className={'botstate ' + (ok ? 'ok' : 'bad')}><Dot on={ok} />{ok ? (item.kind === 'api' ? t('ig.reachable') : t('ig.running')) : t('ig.error')}</span>
        )}
      </div>

      {guide ? (
        <>
          <ol className="keysteps">
            <li><span>{t('ig.step1_pre')}<b>@BotFather</b>: <code>/newbot</code>{t('ig.step1_post')}</span></li>
            <li><span>{t('ig.step2')}</span></li>
            <li><span>{t('ig.step3')}</span></li>
          </ol>
          <p className="keywhy">{t('ig.why')}</p>
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
            <p className="keycard__restart" role="status"><span className="spin" />{t('ig.restarting')}</p>
          ) : (
            <div className="keycard__act">
              <Button kind="tonal" size="s" icon="key" disabled={busy} onClick={() => onChange(null)}>{t('ig.change_key')}</Button>
              {item.source === 'page' && <Button kind="plain" size="s" disabled={busy} onClick={onReset}>{t('ig.reset')}</Button>}
            </div>
          )}
        </>
      )}
    </article>
  )
}

export default function Integrations() {
  const { data, error, loading, reload, setData } = useApi<{ items: KeyItem[] }>('admin/integrations')
  const [editing, setEditing] = useState<string | null>(null) // item id whose key sheet is open
  const [restarting, setRestarting] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const alive = useRef(true)
  useEffect(() => () => { alive.current = false }, [])

  const items = data?.items || []

  // A bot restart drops the API for a moment: poll every 2 s for up to 30 s until it answers again.
  async function waitBack(id: string) {
    setRestarting(id)
    const t0 = Date.now()
    let res: { items: KeyItem[] } | null = null
    while (alive.current && Date.now() - t0 < 30000) {
      await sleep(2000)
      try { res = await api<{ items: KeyItem[] }>('admin/integrations'); break } catch (e) {
        if ((e as ApiError).status === 401) { toast(t('ig.key_changed_401'), 'bad'); break }
      }
    }
    if (!alive.current) return
    setRestarting(null)
    if (res) { setData(res); toast(t('ig.restarted')) } else {
      toast(t('ig.no_answer'), 'bad')
      void reload()
    }
  }

  async function saved(id: string, r: KeySaved) {
    setEditing(null)
    setData({ items: items.map((x) => (x.id === id ? r.item : x)) })
    toast(t('ig.key_saved'))
    if (r.restarting) await waitBack(id); else void reload()
  }
  async function reset(item: KeyItem) {
    if (!(await confirmDialog(t('ig.reset_q', { role: role(item.id) })))) return
    setBusy(true)
    try {
      const r = await api<KeySaved>(`admin/integrations/${item.id}/reset`, { method: 'POST' })
      haptic.notify('success')
      setData({ items: items.map((x) => (x.id === item.id ? r.item : x)) })
      toast(t('ig.restored'))
      if (r.restarting) await waitBack(item.id); else void reload()
    } catch (e) { haptic.notify('error'); toast((e as Error).message, 'bad') } finally { setBusy(false) }
  }

  const edit = items.find((x) => x.id === editing)
  return (
    <section className="sec">
      <div className="sec__h"><h3>{t('ig.title')}</h3></div>
      {loading && !data && <Skeleton rows={3} title={false} />}
      {error && !data && <ErrorBox error={error} onRetry={reload} what={t('ig.load_fail')} />}
      <div className="keys">
        {items.map((it) => (
          <KeyCard key={it.id} item={it} busy={busy || !!restarting} restarting={restarting === it.id}
            onChange={(r) => { if (r) void saved(it.id, r); else setEditing(it.id) }} onReset={() => { void reset(it) }} />
        ))}
      </div>
      <Sheet open={!!edit} title={t('ig.change_key')} onClose={() => setEditing(null)}>
        {edit && (
          <>
            <p className="sheet__sub">{role(edit.id)}{edit.bot?.username ? ` · @${edit.bot.username}` : ''}</p>
            <KeyForm item={edit} autoFocus onSaved={(r) => { void saved(edit.id, r) }} />
          </>
        )}
      </Sheet>
    </section>
  )
}
