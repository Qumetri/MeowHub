import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { CATEGORIES, TOOLS, type Tool } from '../tools'
import type { OpenTool } from '../App'
import Icon from './Icon'
import Seal from './Seal'
import EngineStatus from './EngineStatus'
import LangSwitch from './LangSwitch'
import { APP_NAME, T, tx } from '../lib/i18n'

const norm = (s: string): string => s.toLowerCase().replace(/ё/g, 'е')

export default function Home({ onOpen }: { onOpen: OpenTool }) {
  const [q, setQ] = useState('')
  const query = useDeferredValue(norm(q.trim()))
  const searchRef = useRef<HTMLInputElement>(null)

  // "/" jumps to the search, like most tool catalogues.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '/' && document.activeElement?.tagName !== 'INPUT') {
        e.preventDefault()
        searchRef.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const groups = useMemo(() => {
    // Both languages match, whichever one the page is showing.
    const words = (x: Tool['title']) => (typeof x === 'string' ? x : `${x.ru} ${x.en}`)
    const hit = (t: Tool) => !query || norm(`${words(t.title)} ${words(t.desc)} ${t.kinds}`).includes(query)
    return CATEGORIES.map((c) => ({ ...c, tools: TOOLS.filter((t) => t.cat === c.id && hit(t)) })).filter((g) => g.tools.length)
  }, [query])

  return (
    <div className="page">
      <LangSwitch />
      <header className="masthead">
        <div className="masthead-text">
          <p className="wordmark">{tx(APP_NAME)}</p>
          <h1>{T('PDF и документы без чужих серверов', 'PDFs and documents, no one else’s servers')}</h1>
          <ul className="promises">
            <li><Icon name="WifiOff" size={18} />{T('Движок, который обрабатывает файлы, отрезан от интернета', 'The engine that processes your files is cut off from the internet')}</li>
            <li><Icon name="MemoryStick" size={18} />{T('Файлы лежат только в оперативной памяти и стираются сразу после ответа', 'Files are kept only in memory and wiped as soon as the answer is sent')}</li>
            <li><Icon name="MonitorSmartphone" size={18} />{T('Инструменты с пометкой «в браузере» вообще не отправляют файл', 'Tools marked “in browser” never send the file anywhere')}</li>
          </ul>
          <EngineStatus />
        </div>
        <Seal
          className="masthead-seal"
          ring={T('★ НЕ ПОКИДАЕТ СЕРВЕР ★ НИКАКИХ ОБЛАКОВ ', '★ NEVER LEAVES THE SERVER ★ NO CLOUDS ')}
          center={T('СВОЁ', 'OURS')}
          size={150}
        />
      </header>

      <div className="search">
        <Icon name="Search" size={20} />
        <input
          ref={searchRef}
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={T('Сжать, Word, пароль…', 'Compress, Word, password…')}
          aria-label={T('Найти инструмент', 'Find a tool')}
        />
        <kbd aria-hidden="true">/</kbd>
      </div>

      {groups.length === 0 && (
        <p className="empty-search">{T('Такого инструмента нет. Попробуйте другое слово — например «сжать» или «картинки».', 'No tool by that name. Try another word, such as “compress” or “images”.')}</p>
      )}

      {groups.map((g) => (
        <section key={g.id} className="shelf" aria-labelledby={`cat-${g.id}`}>
          <h2 id={`cat-${g.id}`}>{tx(g.title)}</h2>
          <ul className="tiles">
            {g.tools.map((t) => (
              <li key={t.id}>
                <a className={`tile hue-${g.hue}`} href={`#/${t.id}`} onClick={(e) => { e.preventDefault(); onOpen(t.id) }}>
                  <span className="tile-icon"><Icon name={t.icon} size={22} /></span>
                  <span className="tile-title">{tx(t.title)}</span>
                  <span className="tile-desc">{tx(t.desc)}</span>
                  {t.mode && t.mode !== 'server' && <span className="tag">{T('в браузере', 'in browser')}</span>}
                </a>
              </li>
            ))}
          </ul>
        </section>
      ))}

      <footer className="colophon">
        {T('Работает на ', 'Runs on ')}<a href="https://github.com/Stirling-Tools/Stirling-PDF" target="_blank" rel="noreferrer">Stirling-PDF</a>
        {T(', LibreOffice, Tesseract и Ghostscript на нашем сервере. Страницы в браузере показывает pdf.js.',
          ', LibreOffice, Tesseract and Ghostscript on our own server. Pages in the browser are drawn by pdf.js.')}
      </footer>
    </div>
  )
}
