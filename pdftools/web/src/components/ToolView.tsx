import { Suspense, lazy, useRef, useState } from 'react'
import { CATEGORIES, buildForm, defaults, endpointOf, validate, visibleFields, type FieldValue, type Tool, type Values } from '../tools'
import { ToolError, runTool, type ProgressEvent } from '../lib/api'
import type { OpenTool } from '../App'
import type { ToolBodyProps } from './ImageTool'
import { baseName, isPdf, plural, wrap } from '../lib/format'
import Icon from './Icon'
import Dropzone from './Dropzone'
import FileList from './FileList'
import Field from './Field'
import Result, { type ResultItem } from './Result'
import ImageTool from './ImageTool'

// pdf.js is ~1.5 MB: load it only on screens that show pages.
const PagePicker = lazy(() => import('./PagePicker.jsx'))
const Organizer = lazy(() => import('./Organizer.jsx'))

export function ToolHeader({ tool, onOpen }: { tool: Tool; onOpen: OpenTool }) {
  const cat = CATEGORIES.find((c) => c.id === tool.cat)
  return (
    <header className={`tool-head hue-${cat?.hue ?? 'ink'}`}>
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

// Which request of how many, and where it is: the network phases come from runTool's progress.
type BusyState = { i: number; n: number } & (ProgressEvent | { phase: 'upload'; ratio: number })

function Progress({ state, slow, onCancel }: { state: BusyState; slow?: boolean; onCancel: () => void }) {
  const { i, n, phase } = state
  const ratio = 'ratio' in state ? state.ratio : null
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

function ServerTool({ tool, initialFiles, onOpen }: ToolBodyProps) {
  const [items, setItems] = useState(() => wrap(initialFiles || []))
  const [values, setValues] = useState<Values>(() => defaults(tool))
  const [busy, setBusy] = useState<BusyState | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<ResultItem[] | null>(null)
  const ctl = useRef<AbortController | null>(null)

  const multiple = tool.files !== 'one'
  const fields = visibleFields(tool, values)
  const pagesField = fields.find((f) => f.type === 'pages')
  const only = items.length === 1 ? items[0] : undefined
  const pickerFile = pagesField && only && isPdf(only.file) ? only.file : null
  const problem = validate(tool, values, items.map((i) => i.file))
  const set = (k: string) => (v: FieldValue) => setValues((s) => ({ ...s, [k]: v }))

  const addFiles = (files: File[]) => {
    setError(null)
    setItems((cur) => (multiple ? [...cur, ...wrap(files)] : wrap(files)))
  }

  const run = async () => {
    if (problem) { setError(problem); return }
    setError(null)
    const abort = new AbortController()
    ctl.current = abort
    const files = items.map((i) => i.file)
    // every job has at least one file: validate() demanded it
    const jobs = tool.files === 'many' ? [files] : files.map((f) => [f])
    const out: ResultItem[] = []
    try {
      for (const [i, job] of jobs.entries()) {
        const first = job[0]!
        setBusy({ i, n: jobs.length, phase: 'upload', ratio: 0 })
        const r = await runTool(endpointOf(tool, values), buildForm(tool, values, job), {
          signal: abort.signal,
          fallbackName: `${baseName(first.name)}-${tool.id}.pdf`,
          onProgress: (p) => setBusy((b) => (b ? { ...b, ...p } : b)),
        })
        out.push({ ...r, source: jobs.length > 1 || tool.files !== 'many' ? first : null })
      }
      setResults(out)
    } catch (err) {
      const e = err as Error
      if (e.name !== 'AbortError') {
        const which = jobs.length > 1 ? ` (файл «${jobs[out.length]?.[0]?.name}»)` : ''
        const hint = e instanceof ToolError && e.code === 'password' && tool.id !== 'unlock' ? ' Сначала снимите его инструментом «Снять пароль».' : ''
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
              {pickerFile && pagesField && (
                <Suspense fallback={<p className="note" role="status">Открываю страницы…</p>}>
                  <PagePicker
                    file={pickerFile}
                    value={String(values[pagesField.key] ?? '')}
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

export default function ToolView({ tool, initialFiles, onOpen }: ToolBodyProps) {
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
