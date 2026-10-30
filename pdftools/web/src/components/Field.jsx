import { useId } from 'react'

const POSITIONS = [
  ['1', 'Сверху слева'], ['2', 'Сверху по центру'], ['3', 'Сверху справа'],
  ['4', 'Посередине слева'], ['5', 'В центре'], ['6', 'Посередине справа'],
  ['7', 'Снизу слева'], ['8', 'Снизу по центру'], ['9', 'Снизу справа'],
]

// One option control. Labels are always visible; hints sit under the control.
export default function Field({ f, value, onChange }) {
  const id = useId()
  const hintId = f.hint ? `${id}-hint` : undefined
  const hint = f.hint && <p className="field-hint" id={hintId}>{f.hint}</p>

  if (f.type === 'toggle') {
    return (
      <div className="field field-toggle">
        <label className="switch">
          <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} aria-describedby={hintId} />
          <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
          <span className="switch-label">{f.label}</span>
        </label>
        {hint}
      </div>
    )
  }

  if (f.type === 'segment') {
    const active = f.options.find((o) => o.v === value)
    return (
      <fieldset className="field">
        <legend className="field-label">{f.label}</legend>
        <div className="segment" role="radiogroup">
          {f.options.map((o) => (
            <label key={o.v} className={`segment-opt${o.v === value ? ' on' : ''}`}>
              <input type="radio" name={id} value={o.v} checked={o.v === value} onChange={() => onChange(o.v)} />
              {o.l}
            </label>
          ))}
        </div>
        {active?.hint && <p className="field-hint">{active.hint}</p>}
        {hint}
      </fieldset>
    )
  }

  if (f.type === 'chips') {
    const set = new Set(value || [])
    const toggle = (v) => {
      const next = new Set(set)
      next.has(v) ? next.delete(v) : next.add(v)
      onChange(f.options.map((o) => o.v).filter((x) => next.has(x)))
    }
    return (
      <fieldset className="field">
        <legend className="field-label">{f.label}</legend>
        <div className="chips">
          {f.options.map((o) => (
            <label key={o.v} className={`chip${set.has(o.v) ? ' on' : ''}`}>
              <input type="checkbox" checked={set.has(o.v)} onChange={() => toggle(o.v)} />
              {o.l}
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
        <legend className="field-label">{f.label}</legend>
        <div className="position-grid" role="radiogroup">
          {POSITIONS.map(([v, l]) => (
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
    const shown = f.pct ? `${Math.round(value * 100)}%` : value
    return (
      <div className="field">
        <label className="field-label" htmlFor={id}>{f.label} <output htmlFor={id}>{shown}</output></label>
        <input id={id} type="range" min={f.min} max={f.max} step={f.step} value={value} onChange={(e) => onChange(+e.target.value)} />
      </div>
    )
  }

  if (f.type === 'color') {
    return (
      <div className="field field-inline">
        <label className="field-label" htmlFor={id}>{f.label}</label>
        <input id={id} type="color" value={value} onChange={(e) => onChange(e.target.value)} />
      </div>
    )
  }

  const common = {
    id,
    value: value ?? '',
    placeholder: f.placeholder,
    'aria-describedby': hintId,
    onChange: (e) => onChange(f.type === 'number' ? (e.target.value === '' ? '' : +e.target.value) : e.target.value),
  }
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>{f.label}{f.optional && <span className="optional"> — необязательно</span>}</label>
      {f.type === 'textarea'
        ? <textarea rows={5} {...common} />
        : <input
            type={f.type === 'number' ? 'number' : f.type === 'password' ? 'password' : 'text'}
            inputMode={f.type === 'number' ? 'numeric' : f.type === 'pages' ? 'numeric' : undefined}
            min={f.min} max={f.max}
            autoComplete={f.autocomplete || 'off'}
            spellCheck={false}
            {...common}
          />}
      {hint}
    </div>
  )
}
