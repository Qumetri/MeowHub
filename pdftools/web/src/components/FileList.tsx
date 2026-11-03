import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors, type DragEndEvent } from '@dnd-kit/core'
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import Icon from './Icon'
import { bytes, type FileItem } from '../lib/format'
import { T } from '../lib/i18n'

interface RowProps {
  item: FileItem
  index: number
  total: number
  sortable: boolean
  onMove: (from: number, to: number) => void
  onRemove: (id: string) => void
}

function Row({ item, index, total, sortable, onMove, onRemove }: RowProps) {
  const s = useSortable({ id: item.id, disabled: !sortable })
  const style = { transform: CSS.Transform.toString(s.transform), transition: s.transition }
  return (
    <li ref={s.setNodeRef} style={style} className={`file-row${s.isDragging ? ' dragging' : ''}`}>
      {sortable && (
        <button type="button" className="grip" aria-label={T(`Перетащить «${item.file.name}»`, `Drag “${item.file.name}”`)} {...s.attributes} {...s.listeners}>
          <Icon name="GripVertical" size={18} />
        </button>
      )}
      {sortable && <span className="file-no" aria-hidden="true">{index + 1}</span>}
      <span className="file-name" title={item.file.name}>{item.file.name}</span>
      <span className="file-size">{bytes(item.file.size)}</span>
      {sortable && (
        <span className="file-move">
          <button type="button" className="icon-btn" disabled={index === 0} onClick={() => onMove(index, index - 1)} aria-label={T('Выше', 'Move up')}>
            <Icon name="ArrowUp" size={16} />
          </button>
          <button type="button" className="icon-btn" disabled={index === total - 1} onClick={() => onMove(index, index + 1)} aria-label={T('Ниже', 'Move down')}>
            <Icon name="ArrowDown" size={16} />
          </button>
        </span>
      )}
      <button type="button" className="icon-btn" onClick={() => onRemove(item.id)} aria-label={T(`Убрать «${item.file.name}»`, `Remove “${item.file.name}”`)}>
        <Icon name="X" size={16} />
      </button>
    </li>
  )
}

// Selected files. For tools that combine files ('many') the order matters, so rows can be
// dragged (mouse, touch, keyboard) or moved with the arrow buttons.
export default function FileList({ items, sortable, onChange }: { items: FileItem[]; sortable: boolean; onChange: (items: FileItem[]) => void }) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )
  const move = (from: number, to: number) => onChange(arrayMove(items, from, to))
  const remove = (id: string) => onChange(items.filter((i) => i.id !== id))
  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (!over || active.id === over.id) return
    move(items.findIndex((i) => i.id === active.id), items.findIndex((i) => i.id === over.id))
  }
  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext items={items.map((i) => i.id)} strategy={verticalListSortingStrategy}>
        <ol className="files" aria-label={T('Выбранные файлы', 'Chosen files')}>
          {items.map((item, i) => (
            <Row key={item.id} item={item} index={i} total={items.length} sortable={sortable} onMove={move} onRemove={remove} />
          ))}
        </ol>
      </SortableContext>
      {sortable && items.length > 1 && <p className="files-hint">{T('Файлы пойдут в этом порядке. Перетащите строку или нажмите стрелки.', 'Files go in this order. Drag a row or use the arrows.')}</p>}
    </DndContext>
  )
}
