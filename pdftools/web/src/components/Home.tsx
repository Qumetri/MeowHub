import { useDeferredValue, useEffect, useMemo, useRef, useState } from 'react'
import { CATEGORIES, TOOLS, type Tool } from '../tools'
import type { OpenTool } from '../App'
import Icon from './Icon'
import Seal from './Seal'
import EngineStatus from './EngineStatus'

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
    const hit = (t: Tool) => !query || norm(`${t.title} ${t.desc} ${t.kinds}`).includes(query)
    return CATEGORIES.map((c) => ({ ...c, tools: TOOLS.filter((t) => t.cat === c.id && hit(t)) })).filter((g) => g.tools.length)
  }, [query])

  return (
    <div className="page">
      <header className="masthead">
        <div className="masthead-text">
          <p className="wordmark">Канцелярия</p>
          <h1>PDF и документы без чужих серверов</h1>
          <ul className="promises">
            <li><Icon name="WifiOff" size={18} />Движок, который обрабатывает файлы, отрезан от интернета</li>
            <li><Icon name="MemoryStick" size={18} />Файлы лежат только в оперативной памяти и стираются сразу после ответа</li>
            <li><Icon name="MonitorSmartphone" size={18} />Инструменты с пометкой «в браузере» вообще не отправляют файл</li>
          </ul>
          <EngineStatus />
        </div>
        <Seal className="masthead-seal" ring="★ НЕ ПОКИДАЕТ СЕРВЕР ★ НИКАКИХ ОБЛАКОВ " center="СВОЁ" size={150} />
      </header>

      <div className="search">
        <Icon name="Search" size={20} />
        <input
          ref={searchRef}
          type="search"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Сжать, Word, пароль…"
          aria-label="Найти инструмент"
        />
        <kbd aria-hidden="true">/</kbd>
      </div>

      {groups.length === 0 && (
        <p className="empty-search">Такого инструмента нет. Попробуйте другое слово — например «сжать» или «картинки».</p>
      )}

      {groups.map((g) => (
        <section key={g.id} className="shelf" aria-labelledby={`cat-${g.id}`}>
          <h2 id={`cat-${g.id}`}>{g.title}</h2>
          <ul className="tiles">
            {g.tools.map((t) => (
              <li key={t.id}>
                <a className={`tile hue-${g.hue}`} href={`#/${t.id}`} onClick={(e) => { e.preventDefault(); onOpen(t.id) }}>
                  <span className="tile-icon"><Icon name={t.icon} size={22} /></span>
                  <span className="tile-title">{t.title}</span>
                  <span className="tile-desc">{t.desc}</span>
                  {t.mode && t.mode !== 'server' && <span className="tag">в браузере</span>}
                </a>
              </li>
            ))}
          </ul>
        </section>
      ))}

      <footer className="colophon">
        Работает на <a href="https://github.com/Stirling-Tools/Stirling-PDF" target="_blank" rel="noreferrer">Stirling-PDF</a>,
        LibreOffice, Tesseract и Ghostscript на нашем сервере. Страницы в браузере показывает pdf.js.
      </footer>
    </div>
  )
}
