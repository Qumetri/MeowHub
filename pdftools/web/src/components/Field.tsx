import { useId, type ChangeEvent } from 'react'
import type { Field as FieldSpec, FieldValue } from '../tools'
import { L, T, tx, type Text } from '../lib/i18n'

const POSITIONS: [value: string, label: Text][] = [
  ['1', L('Сверху слева', 'Top left')], ['2', L('Сверху по центру', 'Top center')], ['3', L('Сверху справа', 'Top right')],
  ['4', L('Посередине слева', 'Middle left')], ['5', L('В центре', 'Center')], ['6', L('Посередине справа', 'Middle right')],
  ['7', L('Снизу слева', 'Bottom left')], ['8', L('Снизу по центру', 'Bottom center')], ['9', L('Снизу справа', 'Bottom right')],
]

// One option control. Labels are always visible; hints sit under the control.
interface Props { f: FieldSpec; value: FieldValue | undefined; onChange: (v: FieldValue) => void }

export default function Field({ f, value, onChange }: Props) {
  const id = useId()
  const hintId = f.hint ? `${id}-hint` : undefined
  const hint = f.hint && <p className="field-hint" id={hintId}>{tx(f.hint)}</p>
  const label = tx(f.label)

  if (f.type === 'toggle') {
    return (
      <div className="field field-toggle">
        <label className="switch">
          <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} aria-describedby={hintId} />
          <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
          <span className="switch-label">{label}</span>
        </label>
        {hint}
      </div>
    )
  }

  if (f.type === 'segment') {
    const active = f.options.find((o) => o.v === value)
    return (
      <fieldset className="field">
        <legend className="field-label">{label}</legend>
        <div className="segment" role="radiogroup">
          {f.options.map((o) => (
            <label key={o.v} className={`segment-opt${o.v === value ? ' on' : ''}`}>
              <input type="radio" name={id} value={o.v} checked={o.v === value} onChange={() => onChange(o.v)} />
              {tx(o.l)}
            </label>
          ))}
        </div>
        {active?.hint && <p className="field-hint">{tx(active.hint)}</p>}
        {hint}
      </fieldset>
    )
  }

  if (f.type === 'chips') {
    const set = new Set(Array.isArray(value) ? value : [])
    const toggle = (v: string) => {
      const next = new Set(set)
      if (next.has(v)) next.delete(v); else next.add(v)
      const opts = f.options
      onChange(opts.map((o) => o.v).filter((x) => next.has(x)))
    }
    return (
      <fieldset className="field">
        <legend className="field-label">{label}</legend>
        <div className="chips">
          {f.options.map((o) => (
            <label key={o.v} className={`chip${set.has(o.v) ? ' on' : ''}`}>
              <input type="checkbox" checked={set.has(o.v)} onChange={() => toggle(o.v)} />
              {tx(o.l)}
            </label>
          ))}
        </div>
        {hint}
      </fieldset>
    )
  }

  if (f.type === 'position') {
    return (
      <fieldset className="field">
        <legend className="field-label">{label}</legend>
        <div className="position-grid" role="radiogroup">
          {POSITIONS.map(([v, text]) => [v, tx(text)] as const).map(([v, l]) => (
            <label key={v} className={`position-cell${v === value ? ' on' : ''}`} title={l}>
              <input type="radio" name={id} value={v} checked={v === value} onChange={() => onChange(v)} aria-label={l} />
              <span aria-hidden="true" />
            </label>
          ))}
        </div>
      </fieldset>
    )
  }

  if (f.type === 'range') {
    const n = Number(value)
    const shown = f.pct ? `${Math.round(n * 100)}%` : n
    return (
      <div className="field">
        <label className="field-label" htmlFor={id}>{label} <output htmlFor={id}>{shown}</output></label>
        <input id={id} type="range" min={f.min} max={f.max} step={f.step} value={n} onChange={(e) => onChange(+e.target.value)} />
      </div>
    )
  }

  if (f.type === 'color') {
    return (
      <div className="field field-inline">
        <label className="field-label" htmlFor={id}>{label}</label>
        <input id={id} type="color" value={String(value ?? '')} onChange={(e) => onChange(e.target.value)} />
      </div>
    )
  }

  const common = {
    id,
    value: typeof value === 'number' ? value : String(value ?? ''),
    placeholder: f.placeholder && tx(f.placeholder),
    'aria-describedby': hintId,
    onChange: (e: ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) => onChange(f.type === 'number' ? (e.target.value === '' ? '' : +e.target.value) : e.target.value),
  }
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>{label}{f.optional && <span className="optional">{T(' — необязательно', ' (optional)')}</span>}</label>
      {f.type === 'textarea'
        ? <textarea rows={5} {...common} />
        : <input
            type={f.type === 'number' ? 'number' : f.type === 'password' ? 'password' : 'text'}
            inputMode={f.type === 'number' ? 'numeric' : f.type === 'pages' ? 'numeric' : undefined}
            min={f.type === 'number' ? f.min : undefined} max={f.type === 'number' ? f.max : undefined}
            autoComplete={f.autocomplete || 'off'}
            spellCheck={false}
            {...common}
          />}
      {hint}
    </div>
  )
}
