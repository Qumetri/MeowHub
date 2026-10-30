import { Suspense, lazy, useRef, useState } from 'react'
import { CATEGORIES, buildForm, defaults, endpointOf, validate, visibleFields } from '../tools.js'
import { runTool } from '../lib/api.js'
import { baseName, isPdf, plural, wrap } from '../lib/format.js'
import Icon from './Icon.jsx'
import Dropzone from './Dropzone.jsx'
import FileList from './FileList.jsx'
import Field from './Field.jsx'
import Result from './Result.jsx'
import ImageTool from './ImageTool.jsx'

// pdf.js is ~1.5 MB: load it only on screens that show pages.
const PagePicker = lazy(() => import('./PagePicker.jsx'))
const Organizer = lazy(() => import('./Organizer.jsx'))

export function ToolHeader({ tool, onOpen }) {
  const cat = CATEGORIES.find((c) => c.id === tool.cat)
  return (
    <header className={`tool-head hue-${cat.hue}`}>
      <a className="back" href="#" onClick={(e) => { e.preventDefault(); onOpen(null) }}>
        <Icon name="ArrowLeft" size={18} />Все инструменты
      </a>
      <div className="tool-title">
        <span className="tile-icon"><Icon name={tool.icon} size={26} /></span>
        <div>
          <h1>{tool.title}</h1>
          <p>{tool.desc}</p>
        </div>
      </div>
    </header>
  )
}

function Progress({ state, slow, onCancel }) {
  const { i, n, phase, ratio } = state
  const what = phase === 'upload' ? `Отправляю на сервер${ratio != null ? ` — ${Math.round(ratio * 100)}%` : ''}`
    : phase === 'download' ? 'Забираю результат' : 'Обрабатываю'
  return (
    <div className="progress" role="status" aria-live="polite">
      <p className="progress-what">{what}{n > 1 ? ` · файл ${i + 1} из ${n}` : ''}</p>
      <div className={`progress-track${phase === 'upload' ? '' : ' busy'}`}>
        <span className="progress-fill" style={phase === 'upload' && ratio != null ? { transform: `scaleX(${ratio})` } : undefined} />
      </div>
      {slow && phase === 'work' && <p className="field-hint">Это может занять пару минут на больших файлах.</p>}
      <button type="button" className="btn btn-quiet" onClick={onCancel}>Отменить</button>
    </div>
  )
}

function ServerTool({ tool, initialFiles, onOpen }) {
  const [items, setItems] = useState(() => wrap(initialFiles || []))
  const [values, setValues] = useState(() => defaults(tool))
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [results, setResults] = useState(null)
  const ctl = useRef(null)

  const multiple = tool.files !== 'one'
  const fields = visibleFields(tool, values)
  const pagesField = fields.find((f) => f.type === 'pages')
  const pickerFile = pagesField && items.length === 1 && isPdf(items[0].file) ? items[0].file : null
  const problem = validate(tool, values, items.map((i) => i.file))
  const set = (k) => (v) => setValues((s) => ({ ...s, [k]: v }))

  const addFiles = (files) => {
    setError(null)
    setItems((cur) => (multiple ? [...cur, ...wrap(files)] : wrap(files)))
  }

  const run = async () => {
    if (problem) { setError(problem); return }
    setError(null)
    ctl.current = new AbortController()
    const files = items.map((i) => i.file)
    const jobs = tool.files === 'many' ? [files] : files.map((f) => [f])
    const out = []
    try {
      for (let i = 0; i < jobs.length; i++) {
        setBusy({ i, n: jobs.length, phase: 'upload', ratio: 0 })
        const r = await runTool(endpointOf(tool, values), buildForm(tool, values, jobs[i]), {
          signal: ctl.current.signal,
          fallbackName: `${baseName(jobs[i][0].name)}-${tool.id}.pdf`,
          onProgress: (p) => setBusy((b) => ({ ...b, ...p })),
        })
        out.push({ ...r, source: jobs.length > 1 || tool.files !== 'many' ? jobs[i][0] : null })
      }
      setResults(out)
    } catch (e) {
      if (e.name !== 'AbortError') {
        const which = jobs.length > 1 ? ` (файл «${jobs[out.length][0].name}»)` : ''
        const hint = e.code === 'password' && tool.id !== 'unlock' ? ' Сначала снимите его инструментом «Снять пароль».' : ''
        setError(`${e.message}${hint}${which}`)
        if (out.length) setResults(out)
      }
    } finally {
      setBusy(null)
    }
  }

  if (results) {
    return (
      <>
        {error && <p className="alert" role="alert"><Icon name="CircleAlert" size={18} />{error} Остальные файлы готовы.</p>}
        <Result
          tool={tool}
          results={results}
          where="server"
          onRestart={() => { setResults(null); setItems([]); setError(null) }}
          onAdjust={() => { setResults(null); setError(null) }}
          onNext={onOpen}
        />
      </>
    )
  }

  return (
    <div className="workbench">
      <div className="bench-main">
        {items.length === 0
          ? <Dropzone accept={tool.accept} kinds={tool.kinds} multiple={multiple} onFiles={addFiles} />
          : (
            <>
              <FileList items={items} sortable={tool.files === 'many'} onChange={setItems} />
              {multiple && <Dropzone accept={tool.accept} kinds={tool.kinds} multiple compact onFiles={addFiles} />}
              {pickerFile && (
                <Suspense fallback={<p className="note" role="status">Открываю страницы…</p>}>
                  <PagePicker
                    file={pickerFile}
                    value={values[pagesField.key]}
                    onChange={set(pagesField.key)}
                    verb={pagesField.label}
                  />
                </Suspense>
              )}
            </>
          )}
      </div>

      <aside className="bench-side" aria-label="Настройки">
        {fields.length > 0 && (
          <div className="options">
            {fields.map((f) => <Field key={f.key} f={f} value={values[f.key]} onChange={set(f.key)} />)}
          </div>
        )}
        <div className="go">
          {error && <p className="alert" role="alert"><Icon name="CircleAlert" size={18} />{error}</p>}
          {busy
            ? <Progress state={busy} slow={tool.slow} onCancel={() => ctl.current?.abort()} />
            : (
              <button type="button" className="btn btn-ink btn-big" onClick={run} aria-disabled={!!problem}>
                {tool.action}
                {items.length > 1 && tool.files === 'batch' ? ` · ${items.length} ${plural(items.length, ['файл', 'файла', 'файлов'])}` : ''}
              </button>
            )}
          {!busy && problem && items.length > 0 && <p className="field-hint">{problem}</p>}
        </div>
      </aside>
    </div>
  )
}

export default function ToolView({ tool, initialFiles, onOpen }) {
  const Body = tool.mode === 'organize' ? Organizer : tool.mode === 'image' ? ImageTool : ServerTool
  return (
    <div className="page">
      <ToolHeader tool={tool} onOpen={onOpen} />
      <Suspense fallback={<p className="note" role="status">Загружаю…</p>}>
        <Body tool={tool} initialFiles={initialFiles} onOpen={onOpen} />
      </Suspense>
    </div>
  )
}
