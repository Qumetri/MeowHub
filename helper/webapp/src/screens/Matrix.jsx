import { useState } from 'react'
import Icon from '../icons.jsx'
import { Action, Button, Cell, Empty, ErrorBox, Field, Pill, Section, Sheet, Skeleton, doCopy, toast } from '../ui.jsx'
import { api } from '../api.js'
import { useApi } from '../hooks.js'
import { t } from '../i18n.js'
import { haptic, openLink } from '../tg.js'
import { useApp } from '../ctx.js'
import { Title } from './shared.jsx'

const ELEMENT_IOS = 'https://apps.apple.com/app/element-x-secure-chat-call/id1631335820'
const ELEMENT_ANDROID = 'https://play.google.com/store/apps/details?id=io.element.android.x'
const NAME_RE = /^[a-z0-9._=-]{3,24}$/

function PasswordInput({ value, onChange, placeholder, show, label }) {
  return (
    <input className="input" type={show ? 'text' : 'password'} value={value} placeholder={placeholder} aria-label={label}
      autoComplete="new-password" autoCapitalize="off" autoCorrect="off" spellCheck={false}
      onChange={(e) => onChange(e.target.value)} />
  )
}

function ShowToggle({ show, onToggle }) {
  return (
    <button type="button" className="iconbtn iconbtn--in" onClick={onToggle} aria-label={show ? t('common.hide') : t('common.show')}>
      <Icon name={show ? 'eyeoff' : 'eye'} size={18} />
    </button>
  )
}

function ChangePassword({ mxid, onClose }) {
  const [pw, setPw] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const ok = pw.length >= 10
  async function save() {
    if (!ok || busy) return
    setBusy(true); setErr('')
    try {
      await api('matrix/password', { method: 'POST', body: { mxid, password: pw } })
      haptic.notify('success'); toast(t('mx.pw_changed')); onClose()
    } catch (e) {
      haptic.notify('error'); setErr(e.code === 'weak_password' ? t('mx.err.weak_password') : e.message)
    } finally { setBusy(false) }
  }
  return (
    <>
      <p className="sheet__sub mono">{mxid}</p>
      <Field label={t('mx.new_pw')} error={err || (pw && !ok ? t('mx.password_short') : '')}
        suffix={<ShowToggle show={show} onToggle={() => setShow(!show)} />}>
        <PasswordInput value={pw} onChange={(v) => { setPw(v); setErr('') }} show={show} label={t('mx.new_pw')} />
      </Field>
      <Button block onClick={save} disabled={!ok} loading={busy}>{t('common.save')}</Button>
    </>
  )
}

function Created({ info, data, onClose }) {
  return (
    <>
      <Section>
        <Cell icon="globe" title={t('mx.server')} sub={<span className="mono">{data.server_name}</span>}
          right={<Button kind="tonal" size="s" icon="copy" onClick={() => doCopy(data.server_name)}>{t('common.copy')}</Button>} />
        <Cell icon="user" title={t('mx.login')} sub={<span className="mono">{info.username}</span>}
          right={<Button kind="tonal" size="s" icon="copy" onClick={() => doCopy(info.username)}>{t('common.copy')}</Button>} />
      </Section>
      <div className="warnbox"><Icon name="warning" size={18} /><span>{t('mx.save_pw')}</span></div>
      <Section title={t('mx.get_app')}>
        <Cell icon="external" title={t('mx.element_x_ios')} chevron onClick={() => openLink(ELEMENT_IOS)} />
        <Cell icon="external" title={t('mx.element_x_android')} chevron onClick={() => openLink(ELEMENT_ANDROID)} />
        <Cell icon="globe" title={t('mx.element_web')} sub={data.client_url} chevron onClick={() => openLink(data.element_url || data.client_url)} />
      </Section>
      <Button kind="tonal" block onClick={onClose}>{t('common.done')}</Button>
    </>
  )
}

export default function Matrix() {
  const { go } = useApp()
  const { data, error, loading, reload } = useApi('matrix')
  const [user, setUser] = useState('')
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [show, setShow] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [pwFor, setPwFor] = useState(null)
  const [created, setCreated] = useState(null)

  if (loading && !data) return <div className="page"><Title>{t('mx.title')}</Title><Skeleton rows={2} /><Skeleton rows={3} /></div>
  if (error && !data) {
    return (
      <div className="page"><Title>{t('mx.title')}</Title>
        {error.code === 'no_access'
          ? <Empty icon="lock" title={t('mx.no_access')} action={<Button icon="key" onClick={() => go('code', null, true)}>{t('home.enter_code')}</Button>} />
          : error.code === 'matrix_unconfigured'
            ? <Empty icon="server" title={t('mx.err.matrix_unconfigured')} />
            : <ErrorBox error={error} onRetry={reload} what={t('err.load')} />}
      </div>
    )
  }

  const accounts = data.accounts || []
  const userOk = NAME_RE.test(user)
  const pwOk = pw.length >= 10
  const same = pw === pw2
  const valid = userOk && pwOk && same && pw2.length > 0

  async function create() {
    if (!valid || busy) return
    haptic.impact('light')
    setBusy(true); setErr('')
    try {
      await api('matrix/create', { method: 'POST', body: { username: user, password: pw } })
      haptic.notify('success')
      setCreated({ username: user })
      setUser(''); setPw(''); setPw2('')
      reload()
    } catch (e) {
      haptic.notify('error')
      const k = 'mx.err.' + e.code
      setErr(t(k) !== k ? t(k) : e.message || t('err.generic'))
    } finally { setBusy(false) }
  }

  return (
    <div className="page">
      <Title>{t('mx.title')}</Title>
      <p className="lead lead--left">{t('mx.intro')}</p>

      <Section title={t('mx.accounts')} footer={t('mx.accounts_foot', { n: accounts.length, max: data.max })}>
        {accounts.length === 0 && <Cell icon="chat" tone="mute" title={t('mx.empty')} />}
        {accounts.map((a) => (
          <Cell key={a.mxid} icon={a.locked ? 'lock' : 'chat'} tone={a.locked ? 'mute' : 'accent'}
            title={<span className="mxid">{a.mxid}</span>}
            sub={a.locked ? t('mx.locked_hint') : undefined}
            right={a.locked ? <Pill tone="bad">{t('mx.locked')}</Pill> : null}>
            <span className="cell__act">
              <Button kind="tonal" size="s" icon="key" onClick={() => { haptic.impact('light'); setPwFor(a.mxid) }}>{t('mx.change_pw')}</Button>
            </span>
          </Cell>
        ))}
      </Section>

      {data.can_create ? (
        <form className="form" onSubmit={(e) => { e.preventDefault(); create() }} noValidate>
          <h3 className="form__h">{t('mx.create')}</h3>
          <Field label={t('mx.username')} suffix={<span className="suffix">:{data.server_name}</span>}
            hint={t('mx.username_hint')} error={user && !userOk ? t('mx.username_bad') : ''}>
            <input className="input" value={user} placeholder="username" autoCapitalize="off" autoCorrect="off" spellCheck={false}
              onChange={(e) => { setUser(e.target.value.toLowerCase()); setErr('') }} />
          </Field>
          <Field label={t('mx.password')} error={pw && !pwOk ? t('mx.password_short') : ''}
            suffix={<ShowToggle show={show} onToggle={() => setShow(!show)} />}>
            <PasswordInput value={pw} onChange={(v) => { setPw(v); setErr('') }} show={show} label={t('mx.password')} />
          </Field>
          <Field label={t('mx.password2')} error={pw2 && !same ? t('mx.password_mismatch') : ''}>
            <PasswordInput value={pw2} onChange={(v) => { setPw2(v); setErr('') }} show={show} label={t('mx.password2')} />
          </Field>
          {err && <p className="formerr" role="alert">{err}</p>}
          <Action text={t('mx.create')} enabled={valid} loading={busy} onClick={create} visible={!pwFor && !created} />
          <button type="submit" hidden />
        </form>
      ) : (
        <p className="foot">{t('mx.limit')}</p>
      )}

      <Sheet open={!!pwFor} title={t('mx.change_pw')} onClose={() => setPwFor(null)}>
        {pwFor && <ChangePassword mxid={pwFor} onClose={() => setPwFor(null)} />}
      </Sheet>
      <Sheet open={!!created} title={t('mx.created')} onClose={() => setCreated(null)}>
        {created && <Created info={created} data={data} onClose={() => setCreated(null)} />}
      </Sheet>
    </div>
  )
}
