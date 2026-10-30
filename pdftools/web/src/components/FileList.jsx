import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors } from '@dnd-kit/core'
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import Icon from './Icon.jsx'
import { bytes } from '../lib/format.js'

function Row({ item, index, total, sortable, onMove, onRemove }) {
  const s = useSortable({ id: item.id, disabled: !sortable })
  const style = { transform: CSS.Transform.toString(s.transform), transition: s.transition }
  return (
    <li ref={s.setNodeRef} style={style} className={`file-row${s.isDragging ? ' dragging' : ''}`}>
      {sortable && (
        <button type="button" className="grip" aria-label={`Перетащить «${item.file.name}»`} {...s.attributes} {...s.listeners}>
          <Icon name="GripVertical" size={18} />
        </button>
      )}
      {sortable && <span className="file-no" aria-hidden="true">{index + 1}</span>}
      <span className="file-name" title={item.file.name}>{item.file.name}</span>
      <span className="file-size">{bytes(item.file.size)}</span>
      {sortable && (
        <span className="file-move">
          <button type="button" className="icon-btn" disabled={index === 0} onClick={() => onMove(index, index - 1)} aria-label="Выше">
            <Icon name="ArrowUp" size={16} />
          </button>
          <button type="button" className="icon-btn" disabled={index === total - 1} onClick={() => onMove(index, index + 1)} aria-label="Ниже">
            <Icon name="ArrowDown" size={16} />
          </button>
        </span>
      )}
      <button type="button" className="icon-btn" onClick={() => onRemove(item.id)} aria-label={`Убрать «${item.file.name}»`}>
        <Icon name="X" size={16} />
      </button>
    </li>
  )
}

// Selected files. For tools that combine files ('many') the order matters, so rows can be
// dragged (mouse, touch, keyboard) or moved with the arrow buttons.
export default function FileList({ items, sortable, onChange }) {
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )
  const move = (from, to) => onChange(arrayMove(items, from, to))
  const remove = (id) => onChange(items.filter((i) => i.id !== id))
  const onDragEnd = ({ active, over }) => {
    if (!over || active.id === over.id) return
    move(items.findIndex((i) => i.id === active.id), items.findIndex((i) => i.id === over.id))
  }
  return (
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext items={items.map((i) => i.id)} strategy={verticalListSortingStrategy}>
        <ol className="files" aria-label="Выбранные файлы">
          {items.map((item, i) => (
            <Row key={item.id} item={item} index={i} total={items.length} sortable={sortable} onMove={move} onRemove={remove} />
          ))}
        </ol>
      </SortableContext>
      {sortable && items.length > 1 && <p className="files-hint">Файлы пойдут в этом порядке. Перетащите строку или нажмите стрелки.</p>}
    </DndContext>
  )
}
