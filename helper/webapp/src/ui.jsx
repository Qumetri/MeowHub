import { useEffect, useRef, useState } from 'react'
import Icon from './icons.jsx'
import { t } from './i18n.js'
import { avatarSrc } from './api.js'
import { haptic, isTg } from './tg.js'
import { useBack, useMainButton } from './hooks.js'
import { copyText, hueOf, initials } from './util.js'

// ---------- toast ----------
let toastSet = null
export function toast(text, kind = 'ok') { toastSet?.({ text, kind, n: Date.now() }) }
export function Toaster() {
  const [x, setX] = useState(null)
  useEffect(() => { toastSet = setX; return () => { toastSet = null } }, [])
  useEffect(() => {
    if (!x) return undefined
    const id = setTimeout(() => setX(null), 1800)
    return () => clearTimeout(id)
  }, [x])
  if (!x) return null
  return (
    <div className="toast" role="status" key={x.n} data-kind={x.kind}>
      <Icon name={x.kind === 'ok' ? 'check' : 'warning'} size={16} strokeWidth={2.4} />
      {x.text}
    </div>
  )
}

// ---------- layout ----------
export function Section({ title, action, footer, children, className = '' }) {
  return (
    <section className={'sec ' + className}>
      {(title || action) && (
        <div className="sec__h"><h3>{title}</h3>{action}</div>
      )}
      <div className="group">{children}</div>
      {footer && <p className="sec__f">{footer}</p>}
    </section>
  )
}

export function Cell({ icon, tone, title, sub, value, chevron, onClick, danger, right, className = '', disabled, lead, children }) {
  const act = !!onClick && !disabled
  const props = act ? {
    role: 'button', tabIndex: 0, onClick,
    onKeyDown: (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(e) } },
  } : {}
  return (
    <div className={`cell ${act ? 'cell--act' : ''} ${danger ? 'cell--danger' : ''} ${disabled ? 'cell--off' : ''} ${(icon || lead) ? 'cell--lead' : ''} ${className}`} {...props}>
      {lead || (icon && <span className={`tile tile--${tone || 'accent'}`}><Icon name={icon} size={18} /></span>)}
      <span className="cell__body">
        <span className="cell__title">{title}</span>
        {sub != null && sub !== '' && <span className="cell__sub">{sub}</span>}
        {children}
      </span>
      {value != null && value !== '' && <span className="cell__value">{value}</span>}
      {right}
      {chevron && <Icon name="chevron" size={18} className="cell__chev" />}
    </div>
  )
}

export function Switch({ checked, onChange, disabled, label }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      className={'switch' + (checked ? ' on' : '')}
      onClick={(e) => { e.stopPropagation(); haptic.select(); onChange(!checked) }}>
      <span />
    </button>
  )
}

export function Avatar({ uid, member, size = 40, src }) {
  const [bad, setBad] = useState(false)
  const url = src === undefined ? (uid != null ? avatarSrc(uid) : null) : src
  const m = member || { first_name: '?' }
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.4, '--h': hueOf(uid ?? m.id ?? 0) }}>
      <span>{initials(m)}</span>
      {url && !bad && <img src={url} alt="" loading="lazy" onError={() => setBad(true)} />}
    </span>
  )
}

export function Pill({ tone = 'mute', children }) {
  return <span className={'pill pill--' + tone}>{children}</span>
}
export function Dot({ on }) { return <span className={'dot' + (on ? ' on' : '')} /> }

export function Skeleton({ rows = 3, title = true }) {
  return (
    <section className="sec sk">
      {title && <div className="sec__h"><span className="sk__bar" style={{ width: 90 }} /></div>}
      <div className="group">
        {Array.from({ length: rows }, (_, i) => (
          <div className="cell cell--lead" key={i}>
            <span className="tile sk__bar" />
            <span className="cell__body">
              <span className="sk__bar" style={{ width: `${55 + ((i * 17) % 30)}%` }} />
              <span className="sk__bar sk__bar--s" style={{ width: `${30 + ((i * 23) % 25)}%` }} />
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}

export function ErrorBox({ error, onRetry, what }) {
  return (
    <div className="errbox" role="alert">
      <Icon name="warning" size={22} />
      <p>{what ? `${what}. ` : ''}{error?.message || t('err.generic')}</p>
      {onRetry && <Button kind="tonal" onClick={onRetry}>{t('common.retry')}</Button>}
    </div>
  )
}

export function Empty({ icon = 'info', title, text, action }) {
  return (
    <div className="empty">
      <span className="empty__ic"><Icon name={icon} size={26} /></span>
      <h4>{title}</h4>
      {text && <p>{text}</p>}
      {action}
    </div>
  )
}

export function Button({ children, kind = 'primary', icon, onClick, loading, disabled, block, type = 'button', size }) {
  return (
    <button type={type} className={`btn btn--${kind} ${block ? 'btn--block' : ''} ${size ? 'btn--' + size : ''}`}
      disabled={disabled || loading} onClick={onClick}>
      {loading ? <span className="spin" /> : icon && <Icon name={icon} size={18} />}
      {children}
    </button>
  )
}

// Primary submit: Telegram's MainButton in tg mode, a normal button in browser.
export function Action({ text, onClick, enabled = true, loading = false, visible = true, kind = 'primary', icon }) {
  useMainButton({ text, onClick, enabled, loading, visible })
  if (isTg || !visible) return null
  return <div className="action"><Button kind={kind} icon={icon} block onClick={onClick} disabled={!enabled} loading={loading}>{text}</Button></div>
}

export function Chips({ items, value, onChange }) {
  return (
    <div className="chips" role="tablist">
      {items.map((it) => (
        <button key={it.id} type="button" role="tab" aria-selected={value === it.id}
          className={'chip' + (value === it.id ? ' on' : '')}
          onClick={() => { haptic.select(); onChange(it.id) }}>
          {it.label}{it.count != null && <em>{it.count}</em>}
        </button>
      ))}
    </div>
  )
}

export function Segmented({ items, value, onChange }) {
  return (
    <div className="seg" role="tablist">
      {items.map((it) => (
        <button key={it.id} type="button" role="tab" aria-selected={value === it.id}
          className={value === it.id ? 'on' : ''} onClick={() => { haptic.select(); onChange(it.id) }}>{it.label}</button>
      ))}
    </div>
  )
}

export function Field({ label, hint, error, suffix, children }) {
  return (
    <label className="field">
      {label && <span className="field__l">{label}</span>}
      <span className={'field__box' + (error ? ' bad' : '')}>{children}{suffix && <span className="field__suffix">{suffix}</span>}</span>
      {(error || hint) && <span className={'field__h' + (error ? ' bad' : '')}>{error || hint}</span>}
    </label>
  )
}

export function Sheet({ open, title, onClose, children }) {
  useBack(onClose, open)
  useEffect(() => {
    if (!open) return undefined
    const k = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [open, onClose])
  if (!open) return null
  return (
    <div className="sheet-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="sheet" role="dialog" aria-modal="true" aria-label={title}>
        <div className="sheet__grab" />
        <div className="sheet__head">
          <h2>{title}</h2>
          <button type="button" className="iconbtn" onClick={onClose} aria-label={t('common.close')}><Icon name="x" size={18} /></button>
        </div>
        <div className="sheet__body">{children}</div>
      </div>
    </div>
  )
}

export function Collapse({ title, count, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="sec">
      <button type="button" className="sec__h sec__h--btn" onClick={() => setOpen(!open)} aria-expanded={open}>
        <h3>{title}{count != null && <em> {count}</em>}</h3>
        <Icon name="chevdown" size={16} className={'rot' + (open ? ' open' : '')} />
      </button>
      {open && children}
    </section>
  )
}

export async function doCopy(text) {
  const ok = await copyText(text)
  haptic.notify(ok ? 'success' : 'error')
  toast(ok ? t('common.copied') : t('err.generic'), ok ? 'ok' : 'bad')
  return ok
}

export function useDebounced(v, ms = 200) {
  const [d, setD] = useState(v)
  const r = useRef()
  useEffect(() => { r.current = setTimeout(() => setD(v), ms); return () => clearTimeout(r.current) }, [v, ms])
  return d
}
