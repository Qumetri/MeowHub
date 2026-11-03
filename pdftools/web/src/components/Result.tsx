import { useEffect, useRef, useState } from 'react'
import { NEXT_STEPS, TOOL_BY_ID, type Tool } from '../tools'
import type { OpenTool } from '../App'
import { bytes, counted, download, isPdf } from '../lib/format'
import { T, locale, tx } from '../lib/i18n'
import { zipFiles } from '../lib/image'
import Icon from './Icon'
import Seal from './Seal'

/** One finished file. `source` is the input it came from, for the size comparison. */
export interface ResultItem { blob: Blob; name: string; source?: File | null }

function Saving({ before, after }: { before: number; after: number }) {
  if (after >= before) {
    return <p className="saving saving-none">
        {bytes(before)} → {bytes(after)}
        {T(': меньше не получилось, файл уже сжат. Оставьте оригинал.', ': it wouldn’t get any smaller, the file is already compressed. Keep the original.')}
      </p>
  }
  const pct = Math.round((1 - after / before) * 100)
  return (
    <div className="saving">
      <div className="saving-bars" aria-hidden="true">
        <span className="bar bar-before" />
        <span className="bar bar-after" style={{ transform: `scaleX(${Math.max(after / before, 0.02)})` }} />
      </div>
      <p><strong>−{pct}%</strong> {bytes(before)} <span className="arrow" aria-label={T('стало', 'now')}>→</span> {bytes(after)}</p>
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

  const date = new Date().toLocaleDateString(locale())
  const single = results.length === 1 ? results[0] ?? null : null
  const nextFile = single && isPdf({ name: single.name, type: single.blob.type }) ? new File([single.blob], single.name, { type: 'application/pdf' }) : null

  const saveAll = async () => {
    setZipping(true)
    try { download(await zipFiles(results), `${tx(tool.title)}.zip`) } finally { setZipping(false) }
  }

  return (
    <section className="result" aria-labelledby="result-title">
      <div className="result-sheet">
        <div className="result-head">
          <h2 id="result-title" ref={heading} tabIndex={-1}>
            {single ? T('Файл готов', 'Your file is ready')
              : T(`Готово ${counted(results.length, ['файл', 'файла', 'файлов'], ['file', 'files'])}`, `${counted(results.length, ['файл', 'файла', 'файлов'], ['file', 'files'])} ready`)}
          </h2>
          <Seal
            className="result-seal"
            ring={`★ ${where === 'browser' ? T('В БРАУЗЕРЕ', 'IN THE BROWSER') : T('НА СВОЁМ СЕРВЕРЕ', 'ON OUR OWN SERVER')} ★ ${date} `}
            center={T('ГОТОВО', 'DONE')}
            size={124}
          />
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
                <Icon name="Download" size={18} />{T('Скачать', 'Download')}
              </button>
            </li>
          ))}
        </ul>

        {results.length > 1 && (
          <button type="button" className="btn btn-ink btn-big" onClick={saveAll} disabled={zipping}>
            <Icon name="Download" size={18} />{zipping ? T('Собираю архив…', 'Zipping…') : T('Скачать всё одним архивом', 'Download all as one ZIP')}
          </button>
        )}

        <div className="result-actions">
          <button type="button" className="btn btn-quiet" onClick={onAdjust}><Icon name="Undo2" size={18} />{T('Поменять настройки', 'Change settings')}</button>
          <button type="button" className="btn btn-quiet" onClick={onRestart}><Icon name="Plus" size={18} />{T('Другой файл', 'Another file')}</button>
        </div>
      </div>

      {nextFile && (
        <div className="next">
          <h3>{T('Сделать с этим файлом ещё что-нибудь', 'Do something else with this file')}</h3>
          <ul className="next-list">
            {NEXT_STEPS.filter((id) => id !== tool.id).flatMap((id) => TOOL_BY_ID[id] ?? []).map((next) => (
              <li key={next.id}>
                <button type="button" className="next-btn" onClick={() => onNext(next.id, [nextFile])}>
                  <Icon name={next.icon} size={18} />{tx(next.title)}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
