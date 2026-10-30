import { useEffect, useState } from 'react'
import { DndContext, KeyboardSensor, PointerSensor, TouchSensor, closestCenter, useSensor, useSensors } from '@dnd-kit/core'
import { SortableContext, arrayMove, rectSortingStrategy, sortableKeyboardCoordinates, useSortable } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { buildPdf, friendlyPdfError, openPdf } from '../lib/pdf.js'
import { baseName, plural } from '../lib/format.js'
import Icon from './Icon.jsx'
import Dropzone from './Dropzone.jsx'
import Thumb from './Thumb.jsx'
import Result from './Result.jsx'

function PageCard({ p, index, doc, onRotate, onDelete }) {
  const s = useSortable({ id: p.key })
  const style = { transform: CSS.Transform.toString(s.transform), transition: s.transition }
  return (
    <li ref={s.setNodeRef} style={style} className={`org-page${s.isDragging ? ' dragging' : ''}`}>
      <div className="org-handle" {...s.attributes} {...s.listeners} aria-label={`Страница ${p.n}, позиция ${index + 1}. Перетащите, чтобы переставить`}>
        <Thumb doc={doc} n={p.n} rotation={p.rot} />
      </div>
      <div className="org-tools">
        <span className="page-no">{index + 1}{p.n !== index + 1 && <small> · была {p.n}</small>}</span>
        <button type="button" className="icon-btn" onClick={() => onRotate(p.key, 270)} aria-label="Повернуть против часовой"><Icon name="RotateCcw" size={16} /></button>
        <button type="button" className="icon-btn" onClick={() => onRotate(p.key, 90)} aria-label="Повернуть по часовой"><Icon name="RotateCw" size={16} /></button>
        <button type="button" className="icon-btn danger" onClick={() => onDelete(p.key)} aria-label="Удалить страницу"><Icon name="Trash2" size={16} /></button>
      </div>
    </li>
  )
}

export default function Organizer({ tool, initialFiles, onOpen }) {
  const [file, setFile] = useState(initialFiles?.[0] || null)
  const [doc, setDoc] = useState(null)
  const [pages, setPages] = useState([])
  const [removed, setRemoved] = useState([])
  const [error, setError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [result, setResult] = useState(null)

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 180, tolerance: 6 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  useEffect(() => {
    if (!file) return
    let alive = true
    setDoc(null)
    setError(null)
    openPdf(file)
      .then((d) => {
        if (!alive) return
        setDoc(d)
        const all = Array.from({ length: d.numPages }, (_, i) => ({ key: `p${i + 1}`, n: i + 1, rot: 0 }))
        setPages(all)
        setRemoved([])
      })
      .catch((e) => alive && setError(friendlyPdfError(e)))
    return () => { alive = false }
  }, [file])

  const rotate = (key, by) => setPages((ps) => ps.map((p) => (p.key === key ? { ...p, rot: (p.rot + by) % 360 } : p)))
  const remove = (key) => {
    setPages((ps) => ps.filter((p) => p.key !== key))
    setRemoved((r) => [...r, pages.find((p) => p.key === key)])
  }
  const undo = () => {
    const last = removed[removed.length - 1]
    setRemoved((r) => r.slice(0, -1))
    setPages((ps) => [...ps, last].sort((a, b) => a.n - b.n))
  }
  const onDragEnd = ({ active, over }) => {
    if (!over || active.id === over.id) return
    setPages((ps) => arrayMove(ps, ps.findIndex((p) => p.key === active.id), ps.findIndex((p) => p.key === over.id)))
  }

  const save = async () => {
    setSaving(true)
    setError(null)
    try {
      const blob = await buildPdf(file, pages)
      setResult([{ blob, name: `${baseName(file.name)}-упорядочено.pdf`, source: file }])
    } catch (e) {
      setError(/encrypt/i.test(String(e)) ? 'Файл зашифрован — сначала снимите пароль.' : 'Не получилось собрать PDF из этих страниц.')
    } finally {
      setSaving(false)
    }
  }

  if (result) {
    return <Result tool={tool} results={result} where="browser" onRestart={() => { setResult(null); setFile(null) }} onAdjust={() => setResult(null)} onNext={onOpen} />
  }
  if (!file) return <Dropzone accept={tool.accept} kinds={tool.kinds} onFiles={(f) => setFile(f[0])} />

  const changed = removed.length > 0 || pages.some((p, i) => p.rot || p.n !== i + 1)

  return (
    <div className="organizer">
      <div className="org-bar">
        <span className="file-name" title={file.name}>{file.name}</span>
        <span role="status">{doc ? `${pages.length} ${plural(pages.length, ['страница', 'страницы', 'страниц'])}` : 'Открываю…'}</span>
        {removed.length > 0 && <button type="button" className="link-btn" onClick={undo}>Вернуть удалённую</button>}
        <button type="button" className="link-btn" onClick={() => setFile(null)}>Другой файл</button>
      </div>
      <p className="files-hint">Перетащите страницу, чтобы переставить. На телефоне — задержите палец на странице. Всё происходит в браузере, файл никуда не отправляется.</p>

      {error && <p className="alert" role="alert"><Icon name="CircleAlert" size={18} />{error}</p>}

      {doc && (
        <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
          <SortableContext items={pages.map((p) => p.key)} strategy={rectSortingStrategy}>
            <ol className="org-grid">
              {pages.map((p, i) => <PageCard key={p.key} p={p} index={i} doc={doc} onRotate={rotate} onDelete={remove} />)}
            </ol>
          </SortableContext>
        </DndContext>
      )}

      <div className="go go-sticky">
        <button type="button" className="btn btn-ink btn-big" onClick={save} disabled={!doc || saving || pages.length === 0}>
          {saving ? 'Собираю…' : tool.action}
        </button>
        {!changed && doc && <p className="field-hint">Пока ничего не изменено.</p>}
      </div>
    </div>
  )
}
