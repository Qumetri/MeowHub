import { useEffect, useMemo, useState } from 'react'
import { openPdf, type PdfDoc } from '../lib/pdf'
import { counted, fromRanges, toRanges } from '../lib/format'
import { T } from '../lib/i18n'
import Thumb from './Thumb'

// Click pages to select them; the selection is written back as "1-3,5" into the pages field,
// so typing in the field and clicking here stay in sync. Selected pages get a highlighter band.
interface Props { file: File; value: string; onChange: (pages: string) => void; verb: string }

export default function PagePicker({ file, value, onChange, verb }: Props) {
  const [doc, setDoc] = useState<PdfDoc | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    setDoc(null)
    setFailed(false)
    openPdf(file).then((d) => { if (alive) setDoc(d) }).catch((e: Error) => { console.warn('pdf open failed', e?.name, e?.message); if (alive) setFailed(true) })
    return () => { alive = false }
  }, [file])

  const count = doc?.numPages || 0
  const selected = useMemo(() => fromRanges(value, count || Infinity), [value, count])

  if (failed) return <p className="note">{T('Страницы этого файла не показать — впишите номера в поле справа.', 'Can’t show this file’s pages: type the numbers into the field instead.')}</p>
  if (!doc) return <p className="note" role="status">{T('Открываю страницы…', 'Opening pages…')}</p>

  const toggle = (n: number) => {
    const next = new Set(selected)
    if (next.has(n)) next.delete(n); else next.add(n)
    onChange(toRanges(next))
  }

  return (
    <div className="picker">
      <div className="picker-bar">
        <span role="status">
          {selected.size
            ? T(`${verb}: ${counted(selected.size, ['страница', 'страницы', 'страниц'], ['page', 'pages'])} из ${count}`,
              `${verb}: ${counted(selected.size, ['страница', 'страницы', 'страниц'], ['page', 'pages'])} of ${count}`)
            : T(`${counted(count, ['страница', 'страницы', 'страниц'], ['page', 'pages'])} — нажмите на нужные`,
              `${counted(count, ['страница', 'страницы', 'страниц'], ['page', 'pages'])}: click the ones you need`)}
        </span>
        <span className="picker-actions">
          <button type="button" className="link-btn" onClick={() => onChange(toRanges(new Set(Array.from({ length: count }, (_, i) => i + 1).filter((n) => n % 2))))}>{T('Нечётные', 'Odd')}</button>
          <button type="button" className="link-btn" onClick={() => onChange(toRanges(new Set(Array.from({ length: count }, (_, i) => i + 1).filter((n) => n % 2 === 0))))}>{T('Чётные', 'Even')}</button>
          {selected.size > 0 && <button type="button" className="link-btn" onClick={() => onChange('')}>{T('Сбросить', 'Clear')}</button>}
        </span>
      </div>
      <ul className="pages">
        {Array.from({ length: count }, (_, i) => i + 1).map((n) => (
          <li key={n}>
            <button
              type="button"
              className={`pg${selected.has(n) ? ' marked' : ''}`}
              aria-pressed={selected.has(n)}
              aria-label={T(`Страница ${n}`, `Page ${n}`)}
              onClick={() => toggle(n)}
            >
              <Thumb doc={doc} n={n} />
              <span className="page-no">{n}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
