import { useEffect, useRef, useState } from 'react'
import { NEXT_STEPS, TOOL_BY_ID, type Tool } from '../tools'
import type { OpenTool } from '../App'
import { bytes, download, isPdf, plural } from '../lib/format'
import { zipFiles } from '../lib/image'
import Icon from './Icon'
import Seal from './Seal'

/** One finished file. `source` is the input it came from, for the size comparison. */
export interface ResultItem { blob: Blob; name: string; source?: File | null }

function Saving({ before, after }: { before: number; after: number }) {
  if (after >= before) {
    return <p className="saving saving-none">{bytes(before)} → {bytes(after)}: меньше не получилось, файл уже сжат. Оставьте оригинал.</p>
  }
  const pct = Math.round((1 - after / before) * 100)
  return (
    <div className="saving">
      <div className="saving-bars" aria-hidden="true">
        <span className="bar bar-before" />
        <span className="bar bar-after" style={{ transform: `scaleX(${Math.max(after / before, 0.02)})` }} />
      </div>
      <p><strong>−{pct}%</strong> {bytes(before)} <span className="arrow" aria-label="стало">→</span> {bytes(after)}</p>
    </div>
  )
}

interface Props {
  tool: Tool
  results: ResultItem[]
  where: 'server' | 'browser'
  onRestart: () => void
  onAdjust: () => void
  onNext: OpenTool
}

export default function Result({ tool, results, where, onRestart, onAdjust, onNext }: Props) {
  const heading = useRef<HTMLHeadingElement>(null)
  const [zipping, setZipping] = useState(false)
  useEffect(() => { heading.current?.focus() }, [])

  const date = new Date().toLocaleDateString('ru-RU')
  const single = results.length === 1 ? results[0] ?? null : null
  const nextFile = single && isPdf({ name: single.name, type: single.blob.type }) ? new File([single.blob], single.name, { type: 'application/pdf' }) : null

  const saveAll = async () => {
    setZipping(true)
    try { download(await zipFiles(results), `${tool.title}.zip`) } finally { setZipping(false) }
  }

  return (
    <section className="result" aria-labelledby="result-title">
      <div className="result-sheet">
        <div className="result-head">
          <h2 id="result-title" ref={heading} tabIndex={-1}>
            {single ? 'Файл готов' : `Готово ${results.length} ${plural(results.length, ['файл', 'файла', 'файлов'])}`}
          </h2>
          <Seal className="result-seal" ring={`★ ${where === 'browser' ? 'В БРАУЗЕРЕ' : 'НА СВОЁМ СЕРВЕРЕ'} ★ ${date} `} center="ГОТОВО" size={124} />
        </div>

        <ul className="result-files">
          {results.map((r, i) => (
            <li key={i} className="result-file">
              <div className="result-file-main">
                <span className="file-name" title={r.name}>{r.name}</span>
                <span className="file-size">{bytes(r.blob.size)}</span>
              </div>
              {tool.compare && r.source && <Saving before={r.source.size} after={r.blob.size} />}
              <button type="button" className={single ? 'btn btn-ink btn-big' : 'btn btn-quiet'} onClick={() => download(r.blob, r.name)}>
                <Icon name="Download" size={18} />Скачать
              </button>
            </li>
          ))}
        </ul>

        {results.length > 1 && (
          <button type="button" className="btn btn-ink btn-big" onClick={saveAll} disabled={zipping}>
            <Icon name="Download" size={18} />{zipping ? 'Собираю архив…' : 'Скачать всё одним архивом'}
          </button>
        )}

        <div className="result-actions">
          <button type="button" className="btn btn-quiet" onClick={onAdjust}><Icon name="Undo2" size={18} />Поменять настройки</button>
          <button type="button" className="btn btn-quiet" onClick={onRestart}><Icon name="Plus" size={18} />Другой файл</button>
        </div>
      </div>

      {nextFile && (
        <div className="next">
          <h3>Сделать с этим файлом ещё что-нибудь</h3>
          <ul className="next-list">
            {NEXT_STEPS.filter((id) => id !== tool.id).flatMap((id) => TOOL_BY_ID[id] ?? []).map((next) => (
              <li key={next.id}>
                <button type="button" className="next-btn" onClick={() => onNext(next.id, [nextFile])}>
                  <Icon name={next.icon} size={18} />{next.title}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
