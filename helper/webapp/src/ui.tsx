import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent, type ReactNode } from 'react'
import Icon, { type IconName } from './icons'
import type { Person, Tone } from './types'
import { t } from './i18n'
import { avatarSrc, loadAuthedImage } from './api'
import { haptic, isTg } from './tg'
import { useBack, useMainButton } from './hooks'
import { copyText, hueOf, initials } from './util'

// ---------- toast ----------
interface ToastMsg { text: string; kind: 'ok' | 'bad'; n: number }
let toastSet: ((m: ToastMsg | null) => void) | null = null
export function toast(text: string, kind: 'ok' | 'bad' = 'ok'): void { toastSet?.({ text, kind, n: Date.now() }) }
export function Toaster() {
  const [x, setX] = useState<ToastMsg | null>(null)
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
interface SectionProps { title?: ReactNode; action?: ReactNode; footer?: ReactNode; children?: ReactNode; className?: string }
export function Section({ title, action, footer, children, className = '' }: SectionProps) {
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

interface CellProps {
  icon?: IconName
  tone?: Tone
  title: ReactNode
  sub?: ReactNode
  value?: ReactNode
  chevron?: boolean
  onClick?: (e: MouseEvent<HTMLDivElement> | KeyboardEvent<HTMLDivElement>) => void
  danger?: boolean
  right?: ReactNode
  className?: string
  disabled?: boolean
  lead?: ReactNode
  children?: ReactNode
}
export function Cell({ icon, tone, title, sub, value, chevron, onClick, danger, right, className = '', disabled, lead, children }: CellProps) {
  const act = !!onClick && !disabled
  const props = act && onClick ? {
    role: 'button', tabIndex: 0, onClick,
    onKeyDown: (e: KeyboardEvent<HTMLDivElement>) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(e) } },
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

interface SwitchProps { checked: boolean; onChange: (on: boolean) => void; disabled?: boolean; label?: string }
export function Switch({ checked, onChange, disabled, label }: SwitchProps) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} disabled={disabled}
      className={'switch' + (checked ? ' on' : '')}
      onClick={(e) => { e.stopPropagation(); haptic.select(); onChange(!checked) }}>
      <span />
    </button>
  )
}

// Resolves an image URL that may need the Telegram auth header (see loadAuthedImage).
export function useAuthedImage(url: string | null | undefined): { src: string | null; bad: boolean } {
  const [state, setState] = useState<{ src: string | null; bad: boolean }>({ src: null, bad: false })
  useEffect(() => {
    let live = true
    setState({ src: null, bad: false })
    if (!url) return
    loadAuthedImage(url).then((src) => { if (live) setState({ src, bad: false }) }, () => { if (live) setState({ src: null, bad: true }) })
    return () => { live = false }
  }, [url])
  return state
}

interface AvatarProps { uid?: number; member?: Person | null; size?: number; src?: string | null }
export function Avatar({ uid, member, size = 40, src }: AvatarProps) {
  const [bad, setBad] = useState(false)
  const raw = src === undefined ? (uid != null ? avatarSrc(uid) : null) : src
  const img = useAuthedImage(raw)
  const url = img.bad ? null : img.src
  const m: Person = member || { first_name: '?' }
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.4, '--h': hueOf(uid ?? m.id ?? 0) } as CSSProperties}>
      <span>{initials(m)}</span>
      {url && !bad && <img src={url} alt="" loading="lazy" onError={() => setBad(true)} />}
    </span>
  )
}

export function Pill({ tone = 'mute', children }: { tone?: Tone; children?: ReactNode }) {
  return <span className={'pill pill--' + tone}>{children}</span>
}
export function Dot({ on }: { on?: boolean }) { return <span className={'dot' + (on ? ' on' : '')} /> }

export function Skeleton({ rows = 3, title = true }: { rows?: number; title?: boolean }) {
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

export function ErrorBox({ error, onRetry, what }: { error: { message?: string } | null; onRetry?: () => void; what?: string }) {
  return (
    <div className="errbox" role="alert">
      <Icon name="warning" size={22} />
      <p>{what ? `${what}. ` : ''}{error?.message || t('err.generic')}</p>
      {onRetry && <Button kind="tonal" onClick={onRetry}>{t('common.retry')}</Button>}
    </div>
  )
}

export function Empty({ icon = 'info', title, text, action }: { icon?: IconName; title: ReactNode; text?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      <span className="empty__ic"><Icon name={icon} size={26} /></span>
      <h4>{title}</h4>
      {text && <p>{text}</p>}
      {action}
    </div>
  )
}

type ButtonKind = 'primary' | 'tonal' | 'plain' | 'danger'
interface ButtonProps {
  children?: ReactNode
  kind?: ButtonKind
  icon?: IconName
  onClick?: (e: MouseEvent<HTMLButtonElement>) => void
  loading?: boolean
  disabled?: boolean
  block?: boolean
  type?: 'button' | 'submit'
  size?: 's'
  /** accessible name when the visible text alone is ambiguous (e.g. a bare price) */
  label?: string
}
export function Button({ children, kind = 'primary', icon, onClick, loading, disabled, block, type = 'button', size, label }: ButtonProps) {
  return (
    <button type={type} className={`btn btn--${kind} ${block ? 'btn--block' : ''} ${size ? 'btn--' + size : ''}`}
      disabled={disabled || loading} onClick={onClick} aria-label={label}>
      {loading ? <span className="spin" /> : icon && <Icon name={icon} size={18} />}
      {children}
    </button>
  )
}

// Primary submit: Telegram's MainButton in tg mode, a normal button in browser.
interface ActionProps { text: string; onClick: () => void; enabled?: boolean; loading?: boolean; visible?: boolean; kind?: ButtonKind; icon?: IconName }
export function Action({ text, onClick, enabled = true, loading = false, visible = true, kind = 'primary', icon }: ActionProps) {
  useMainButton({ text, onClick, enabled, loading, visible })
  if (isTg || !visible) return null
  return <div className="action"><Button kind={kind} icon={icon} block onClick={onClick} disabled={!enabled} loading={loading}>{text}</Button></div>
}

export interface Choice<T extends string = string> { id: T; label: ReactNode; count?: number }
export function Chips<T extends string>({ items, value, onChange }: { items: Choice<T>[]; value: T; onChange: (id: T) => void }) {
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

export function Segmented<T extends string>({ items, value, onChange }: { items: Choice<T>[]; value: T; onChange: (id: T) => void }) {
  return (
    <div className="seg" role="tablist">
      {items.map((it) => (
        <button key={it.id} type="button" role="tab" aria-selected={value === it.id}
          className={value === it.id ? 'on' : ''} onClick={() => { haptic.select(); onChange(it.id) }}>{it.label}</button>
      ))}
    </div>
  )
}

export function Field({ label, hint, error, suffix, children }: { label?: ReactNode; hint?: ReactNode; error?: ReactNode; suffix?: ReactNode; children?: ReactNode }) {
  return (
    <label className="field">
      {label && <span className="field__l">{label}</span>}
      <span className={'field__box' + (error ? ' bad' : '')}>{children}{suffix && <span className="field__suffix">{suffix}</span>}</span>
      {(error || hint) && <span className={'field__h' + (error ? ' bad' : '')}>{error || hint}</span>}
    </label>
  )
}

export function Sheet({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children?: ReactNode }) {
  useBack(onClose, open)
  useEffect(() => {
    if (!open) return undefined
    const k = (e: globalThis.KeyboardEvent) => { if (e.key === 'Escape') onClose() }
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

export function Collapse({ title, count, children, defaultOpen = false }: { title: ReactNode; count?: number; children?: ReactNode; defaultOpen?: boolean }) {
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

export async function doCopy(text: string): Promise<boolean> {
  const ok = await copyText(text)
  haptic.notify(ok ? 'success' : 'error')
  toast(ok ? t('common.copied') : t('err.generic'), ok ? 'ok' : 'bad')
  return ok
}

export function useDebounced<T>(v: T, ms = 200): T {
  const [d, setD] = useState(v)
  const r = useRef(0)
  useEffect(() => { r.current = window.setTimeout(() => setD(v), ms); return () => clearTimeout(r.current) }, [v, ms])
  return d
}
