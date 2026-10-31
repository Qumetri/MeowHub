import { useRef, useState } from 'react'
import Icon from './Icon'

function accepts(file: File, accept: string | undefined): boolean {
  if (!accept) return true
  const name = file.name.toLowerCase()
  return accept.split(',').some((raw) => {
    const a = raw.trim().toLowerCase()
    if (a.startsWith('.')) return name.endsWith(a)
    if (a.endsWith('/*')) return file.type.startsWith(a.slice(0, -1))
    return file.type === a
  })
}

// The "tray": drop files anywhere on it, or pick them. Rejects other types with a reason.
interface Props {
  accept?: string
  kinds: string
  multiple?: boolean
  compact?: boolean
  onFiles: (files: File[]) => void
}

export default function Dropzone({ accept, kinds, multiple, compact, onFiles }: Props) {
  const input = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const [rejected, setRejected] = useState<string | null>(null)

  const take = (list: FileList | null) => {
    if (!list) return
    const all = [...list]
    const ok = all.filter((f) => accepts(f, accept))
    const bad = all.length - ok.length
    setRejected(bad ? `${bad === 1 ? 'Один файл не подошёл' : `Не подошло файлов: ${bad}`} — нужен ${kinds}.` : null)
    if (ok.length) onFiles(multiple ? ok : ok.slice(0, 1))
  }

  return (
    <div
      className={`tray${over ? ' tray-over' : ''}${compact ? ' tray-compact' : ''}`}
      onDragOver={(e) => { e.preventDefault(); setOver(true) }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node | null)) setOver(false) }}
      onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files) }}
    >
      <input
        ref={input}
        type="file"
        accept={accept}
        multiple={multiple}
        hidden
        onChange={(e) => { take(e.target.files); e.target.value = '' }}
      />
      {compact ? (
        <button type="button" className="btn btn-quiet" onClick={() => input.current?.click()}>
          <Icon name="Plus" size={18} />Добавить ещё
        </button>
      ) : (
        <>
          <span className="tray-icon"><Icon name="Upload" size={28} /></span>
          <p className="tray-title">Перетащите сюда {multiple ? 'файлы' : 'файл'}</p>
          <p className="tray-hint">{kinds}{multiple ? ', можно несколько' : ''}</p>
          <button type="button" className="btn btn-ink" onClick={() => input.current?.click()}>
            Выбрать {multiple ? 'файлы' : 'файл'}
          </button>
        </>
      )}
      {rejected && <p className="tray-error" role="alert">{rejected}</p>}
    </div>
  )
}
