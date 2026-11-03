import { useState } from 'react'
import { convertImage, type ImageFormat } from '../lib/image'
import type { Field as FieldSpec, Tool, Values } from '../tools'
import type { OpenTool } from '../App'
import type { ResultItem } from './Result'
import { counted, wrap } from '../lib/format'
import { L, T, tx } from '../lib/i18n'
import Dropzone from './Dropzone'
import FileList from './FileList'
import Field from './Field'
import Result from './Result'
import Icon from './Icon'

const FIELDS: FieldSpec[] = [
  {
    key: 'format', type: 'segment', label: L('Сохранить как', 'Save as'),
    options: [
      { v: 'jpg', l: 'JPG' },
      { v: 'webp', l: 'WebP', hint: L('Меньше JPG при том же качестве', 'Smaller than JPG at the same quality') },
      { v: 'png', l: 'PNG', hint: L('Без потерь — файл будет большим', 'Lossless, so the file will be big') },
    ],
  },
  { key: 'quality', type: 'range', label: L('Качество', 'Quality'), min: 0.4, max: 0.95, step: 0.05, pct: true },
  {
    key: 'maxSide', type: 'segment', label: L('Длинная сторона', 'Long side'),
    options: [{ v: '0', l: L('Как есть', 'As is') }, { v: '2560', l: '2560' }, { v: '1920', l: '1920' }, { v: '1280', l: '1280' }],
  },
]

export interface ToolBodyProps { tool: Tool; initialFiles: File[] | null; onOpen: OpenTool }

export default function ImageTool({ tool, initialFiles, onOpen }: ToolBodyProps) {
  const [items, setItems] = useState(() => wrap(initialFiles || []))
  const [values, setValues] = useState<Values>({ format: 'jpg', quality: 0.8, maxSide: '1920' })
  const [busy, setBusy] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [results, setResults] = useState<ResultItem[] | null>(null)

  const run = async () => {
    if (!items.length) return
    setError(null)
    const out: ResultItem[] = []
    for (const [i, item] of items.entries()) {
      setBusy(i)
      try {
        const r = await convertImage(item.file, { format: values.format as ImageFormat, quality: Number(values.quality), maxSide: Number(values.maxSide) })
        out.push({ ...r, source: item.file })
      } catch {
        setError(T(`Браузер не смог открыть «${item.file.name}».`, `The browser couldn’t open “${item.file.name}”.`))
      }
    }
    setBusy(null)
    if (out.length) setResults(out)
  }

  if (results) {
    return <Result tool={{ ...tool, compare: true }} results={results} where="browser" onRestart={() => { setResults(null); setItems([]) }} onAdjust={() => setResults(null)} onNext={onOpen} />
  }

  return (
    <div className="workbench">
      <div className="bench-main">
        {items.length === 0
          ? <Dropzone accept={tool.accept} kinds={tool.kinds} multiple onFiles={(f) => setItems(wrap(f))} />
          : (
            <>
              <FileList items={items} sortable={false} onChange={setItems} />
              <Dropzone accept={tool.accept} kinds={tool.kinds} multiple compact onFiles={(f) => setItems((c) => [...c, ...wrap(f)])} />
            </>
          )}
      </div>
      <aside className="bench-side" aria-label={T('Настройки', 'Settings')}>
        <div className="options">
          {FIELDS.filter((f) => f.key !== 'quality' || values.format !== 'png').map((f) => (
            <Field key={f.key} f={f} value={values[f.key]} onChange={(v) => setValues((s) => ({ ...s, [f.key]: v }))} />
          ))}
        </div>
        <div className="go">
          {error && <p className="alert" role="alert"><Icon name="CircleAlert" size={18} />{error}</p>}
          <button type="button" className="btn btn-ink btn-big" onClick={run} disabled={busy != null} aria-disabled={!items.length}>
            {busy != null
              ? T(`Обрабатываю ${busy + 1} из ${items.length}…`, `Working on ${busy + 1} of ${items.length}…`)
              : `${tx(tool.action)}${items.length > 1 ? ` · ${counted(items.length, ['картинка', 'картинки', 'картинок'], ['image', 'images'])}` : ''}`}
          </button>
          {!items.length && <p className="field-hint">{T('Добавьте картинки — они не покинут ваше устройство.', 'Add images. They never leave your device.')}</p>}
        </div>
      </aside>
    </div>
  )
}
