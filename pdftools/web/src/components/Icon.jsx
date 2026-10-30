import {
  Archive, ArrowDown, ArrowLeft, ArrowUp, BookOpen, Check, ChevronRight, CircleAlert, Combine, Download,
  EyeOff, FileMinus, FileOutput, FileText, FileType, FileX, GripVertical, Image, ImageDown, Images, Layers,
  LayoutGrid, ListOrdered, Lock, LockOpen, Mail, MemoryStick, Minimize2, Plus, Presentation, Printer,
  RotateCcw, RotateCw, ScanText, Scissors, Search, Sheet, ShieldCheck, Stamp, Tags, Trash2, Type, Upload,
  WifiOff, Wrench, X, MonitorSmartphone, Undo2,
} from 'lucide-react'

const ICONS = {
  Archive, ArrowDown, ArrowLeft, ArrowUp, BookOpen, Check, ChevronRight, CircleAlert, Combine, Download,
  EyeOff, FileMinus, FileOutput, FileText, FileType, FileX, GripVertical, Image, ImageDown, Images, Layers,
  LayoutGrid, ListOrdered, Lock, LockOpen, Mail, MemoryStick, Minimize2, Plus, Presentation, Printer,
  RotateCcw, RotateCw, ScanText, Scissors, Search, Sheet, ShieldCheck, Stamp, Tags, Trash2, Type, Upload,
  WifiOff, Wrench, X, MonitorSmartphone, Undo2,
}

export default function Icon({ name, size = 20, ...rest }) {
  const C = ICONS[name]
  return C ? <C size={size} strokeWidth={1.8} aria-hidden="true" focusable="false" {...rest} /> : null
}
