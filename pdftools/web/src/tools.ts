// Every tool on the page. A server tool posts its files to the engine (Stirling-PDF) at
// api/v1/<endpoint>; Caddy only lets through the endpoints listed here (pdftools.caddy) —
// adding a tool means adding its endpoint there too.
//
// files: 'one'   — exactly one file per request
//        'batch' — several files allowed, each is sent on its own (results listed one by one)
//        'many'  — all files go in one request, in the order the person arranged them
// mode:  'server' (default), 'organize' and 'image' run entirely in the browser.
//
// The types below are the contract between this list and the form/engine code: a field's
// `type` decides which props it may carry, and an unknown icon or category fails `tsc`.
// Every text a person reads is L(ru, en) (lib/i18n.ts); components read it with tx().

import type { IconName } from './components/Icon'
import { L, T, tx, type Text } from './lib/i18n'

export type FieldValue = string | number | boolean | string[]
export type Values = Record<string, FieldValue>

export interface Option { v: string; l: Text; hint?: Text }

interface FieldBase {
  key: string
  label: Text
  hint?: Text
  /** false: only shapes the request (via `map` or the endpoint), never sent as is */
  send?: boolean
  /** shown (and validated, and sent) only while this returns true */
  show?: (v: Values) => boolean
  required?: boolean
  optional?: boolean
  placeholder?: Text
  autocomplete?: string
}
export type Field = FieldBase & (
  | { type: 'toggle'; def?: boolean }
  | { type: 'segment'; options: Option[]; def?: string }
  | { type: 'chips'; options: Option[]; def?: string[]; min?: number }
  | { type: 'position'; def?: string }
  | { type: 'range'; min: number; max: number; step: number; pct?: boolean; def?: number }
  | { type: 'color'; def?: string }
  | { type: 'number'; min?: number; max?: number; def?: number }
  | { type: 'text' | 'password' | 'textarea' | 'pages'; def?: Text }
)
export type FieldType = Field['type']

export type CategoryId = 'organize' | 'optimize' | 'topdf' | 'frompdf' | 'edit' | 'security' | 'images'
export interface Category { id: CategoryId; title: Text; hue: 'ink' | 'moss' | 'amber' | 'teal' | 'rose' | 'brick' | 'sky' }

export interface Tool {
  id: string
  cat: CategoryId
  icon: IconName
  accept: string
  /** what the tray asks for, in words: "PDF", "Word, Excel…" */
  kinds: string
  files: 'one' | 'batch' | 'many'
  min?: number
  title: Text
  desc: Text
  action: Text
  mode?: 'server' | 'organize' | 'image'
  endpoint?: string | ((v: Values) => string)
  /** always sent as is */
  fixed?: Record<string, string>
  fields?: Field[]
  /** extra params computed from the form; null/undefined/'' are not sent */
  map?: (v: Values) => Record<string, string | null | undefined>
  /** show the size saving on the result */
  compare?: boolean
  /** warn that large files take minutes */
  slow?: boolean
}

export const CATEGORIES: Category[] = [
  { id: 'organize', title: L('Страницы и файлы', 'Pages and files'), hue: 'ink' },
  { id: 'optimize', title: L('Сжатие и распознавание', 'Compression and OCR'), hue: 'moss' },
  { id: 'topdf', title: L('Сделать PDF', 'Make a PDF'), hue: 'amber' },
  { id: 'frompdf', title: L('Из PDF в другой формат', 'From PDF to another format'), hue: 'teal' },
  { id: 'edit', title: L('Оформление', 'Layout'), hue: 'rose' },
  { id: 'security', title: L('Защита и приватность', 'Security and privacy'), hue: 'brick' },
  { id: 'images', title: L('Картинки', 'Images'), hue: 'sky' },
]

const PDF = { accept: '.pdf,application/pdf', kinds: 'PDF' }
const OFFICE = '.doc,.docx,.odt,.rtf,.txt,.xls,.xlsx,.ods,.csv,.ppt,.pptx,.odp,.odg'
const IMAGES = '.jpg,.jpeg,.png,.webp,.gif,.bmp,.tif,.tiff'

// Page lists use the engine's syntax: "1,3,5-9". The picker writes the same thing.
const pagesField = (key: string, label: Text, hint: Text): Field => ({ key, type: 'pages', label, hint, placeholder: L('например 1,3,5-9', 'e.g. 1,3,5-9') })

const MAKE_PDF = L('Сделать PDF', 'Make PDF')
const CONVERT = L('Конвертировать', 'Convert')
const FORMAT = L('Формат', 'Format')
const COLOR = L('Цвет', 'Color')
const TILT = L('Наклон', 'Tilt')

// Language names are written in their own language, so they read the same either way.
export const OCR_LANGS: Option[] = [
  { v: 'rus', l: 'Русский' },
  { v: 'eng', l: 'English' },
  { v: 'ukr', l: 'Українська' },
  { v: 'fin', l: 'Suomi' },
  { v: 'nor', l: 'Norsk' },
  { v: 'swe', l: 'Svenska' },
  { v: 'deu', l: 'Deutsch' },
]

export const TOOLS: Tool[] = [
  // ── Pages and files ────────────────────────────────────────────────
  {
    id: 'merge', cat: 'organize', icon: 'Combine', ...PDF, files: 'many', min: 2,
    title: L('Объединить PDF', 'Merge PDF'),
    desc: L('Склеить несколько файлов в один, в том порядке, который вы зададите.', 'Combine several files into one, in the order you set.'),
    endpoint: 'general/merge-pdfs',
    fixed: { sortType: 'orderProvided', removeCertSign: 'true' },
    fields: [{ key: 'generateToc', type: 'toggle', label: L('Добавить оглавление из имён файлов', 'Add a table of contents from the file names'), def: false }],
    action: L('Объединить', 'Merge'),
  },
  {
    id: 'organize', cat: 'organize', icon: 'LayoutGrid', ...PDF, files: 'one', mode: 'organize',
    title: L('Упорядочить страницы', 'Organize pages'),
    desc: L('Переставить, повернуть и удалить страницы, глядя на них.', 'Reorder, rotate and delete pages while you look at them.'),
    action: L('Сохранить PDF', 'Save PDF'),
  },
  {
    id: 'split', cat: 'organize', icon: 'Scissors', ...PDF, files: 'one',
    title: L('Разделить PDF', 'Split PDF'),
    desc: L('Разрезать документ на части: после нужных страниц, по N страниц или по размеру.', 'Cut a document into parts: after chosen pages, every N pages or by size.'),
    endpoint: (v) => (v.how === 'after' ? 'general/split-pages' : 'general/split-by-size-or-count'),
    fields: [
      {
        key: 'how', type: 'segment', label: L('Как делить', 'How to split'), def: 'after', send: false,
        options: [{ v: 'after', l: L('После страниц', 'After pages') }, { v: 'count', l: L('По N страниц', 'Every N pages') }, { v: 'size', l: L('По размеру', 'By size') }],
      },
      {
        ...pagesField('pageNumbers', L('Резать после страниц', 'Split after pages'), L('Выделите страницы, после которых начинается новая часть.', 'Select the pages after which a new part starts.')),
        show: (v) => v.how === 'after',
      },
      { key: 'pagesEach', type: 'number', label: L('Страниц в каждой части', 'Pages in each part'), def: 5, min: 1, send: false, show: (v) => v.how === 'count' },
      { key: 'sizeEach', type: 'number', label: L('Не больше, МБ', 'At most, MB'), def: 10, min: 1, send: false, show: (v) => v.how === 'size' },
    ],
    map: (v) => (v.how === 'count' ? { splitType: '1', splitValue: String(v.pagesEach) }
      : v.how === 'size' ? { splitType: '0', splitValue: `${v.sizeEach}MB` } : {}),
    action: L('Разделить', 'Split'),
  },
  {
    id: 'remove-pages', cat: 'organize', icon: 'FileMinus', ...PDF, files: 'one',
    title: L('Удалить страницы', 'Remove pages'),
    desc: L('Убрать лишние страницы из документа.', 'Take the pages you don’t need out of a document.'),
    endpoint: 'general/remove-pages',
    fields: [pagesField('pageNumbers', L('Какие страницы удалить', 'Pages to remove'), L('Нажмите на страницы, которые нужно убрать.', 'Click the pages you want gone.'))],
    action: L('Удалить страницы', 'Remove pages'),
  },
  {
    id: 'extract', cat: 'organize', icon: 'FileOutput', ...PDF, files: 'one',
    title: L('Извлечь страницы', 'Extract pages'),
    desc: L('Сохранить выбранные страницы отдельным документом.', 'Save the chosen pages as a document of their own.'),
    endpoint: 'general/rearrange-pages',
    fixed: { customMode: 'CUSTOM' },
    fields: [pagesField('pageNumbers', L('Какие страницы оставить', 'Pages to keep'), L('Страницы попадут в новый файл в порядке номеров.', 'They go into the new file in page-number order.'))],
    action: L('Извлечь', 'Extract'),
  },
  {
    id: 'rotate', cat: 'organize', icon: 'RotateCw', ...PDF, files: 'batch',
    title: L('Повернуть PDF', 'Rotate PDF'),
    desc: L('Повернуть все страницы. Отдельные страницы — в «Упорядочить страницы».', 'Rotate every page. For single pages, use Organize pages.'),
    endpoint: 'general/rotate-pdf',
    fields: [{
      key: 'angle', type: 'segment', label: L('Поворот по часовой', 'Clockwise'), def: '90',
      options: [{ v: '90', l: '90°' }, { v: '180', l: '180°' }, { v: '270', l: '270°' }],
    }],
    action: L('Повернуть', 'Rotate'),
  },
  {
    id: 'page-numbers', cat: 'organize', icon: 'ListOrdered', ...PDF, files: 'batch',
    title: L('Пронумеровать страницы', 'Number pages'),
    desc: L('Поставить номера страниц в нужном углу.', 'Put page numbers in the corner you choose.'),
    endpoint: 'misc/add-page-numbers',
    fixed: { fontType: 'helvetica', pageNumbers: 'all' },
    fields: [
      { key: 'position', type: 'position', label: L('Где на странице', 'Where on the page'), def: '8' },
      {
        // The engine's built-in fonts have no Cyrillic, so only Latin patterns are offered.
        key: 'customText', type: 'segment', label: L('Вид', 'Style'), def: '{n}',
        options: [{ v: '{n}', l: '3' }, { v: '{n} / {total}', l: '3 / 12' }, { v: '- {n} -', l: '- 3 -' }],
      },
      { key: 'startingNumber', type: 'number', label: L('Начать с номера', 'Start at'), def: 1, min: 0 },
      { key: 'fontSize', type: 'number', label: L('Размер шрифта', 'Font size'), def: 11, min: 6, max: 48 },
    ],
    action: L('Пронумеровать', 'Add numbers'),
  },
  {
    id: 'remove-blanks', cat: 'organize', icon: 'FileX', ...PDF, files: 'batch',
    title: L('Убрать пустые страницы', 'Remove blank pages'),
    desc: L('Найти и удалить пустые листы — частая беда сканов с двух сторон.', 'Find and delete empty sheets, the usual leftovers of double-sided scans.'),
    endpoint: 'misc/remove-blanks',
    fixed: { threshold: '10', whitePercent: '99.9' },
    action: L('Убрать пустые', 'Remove blanks'),
  },

  // ── Compression and OCR ────────────────────────────────────────────
  {
    id: 'compress', cat: 'optimize', icon: 'Minimize2', ...PDF, files: 'batch',
    title: L('Сжать PDF', 'Compress PDF'),
    desc: L('Уменьшить файл, чтобы он пролез в почту или на госуслуги.', 'Make a file small enough for an email or an upload form.'),
    endpoint: 'misc/compress-pdf',
    fields: [
      {
        key: 'optimizeLevel', type: 'segment', label: L('Насколько сильно', 'How hard'), def: '5',
        options: [
          { v: '3', l: L('Бережно', 'Gently'), hint: L('Без потери качества, выигрыш небольшой', 'No quality loss, a small gain') },
          { v: '5', l: L('Обычно', 'Normal'), hint: L('Картинки чуть мягче, файл заметно меньше', 'Pictures a little softer, the file much smaller') },
          { v: '8', l: L('Сильно', 'Hard'), hint: L('Максимум экономии, картинки заметно хуже', 'The most saving, pictures visibly worse') },
        ],
      },
      { key: 'grayscale', type: 'toggle', label: L('Сделать чёрно-белым', 'Make it black and white'), def: false },
    ],
    fixed: { linearize: 'false', normalize: 'false' },
    action: L('Сжать', 'Compress'),
    compare: true,
  },
  {
    id: 'ocr', cat: 'optimize', icon: 'ScanText', ...PDF, files: 'batch',
    title: L('Распознать текст (OCR)', 'Recognize text (OCR)'),
    desc: L('Превратить скан в PDF, где текст можно искать и копировать.', 'Turn a scan into a PDF whose text you can search and copy.'),
    endpoint: 'misc/ocr-pdf',
    fields: [
      { key: 'languages', type: 'chips', label: L('Языки документа', 'Document languages'), def: ['rus', 'eng'], options: OCR_LANGS, min: 1 },
      {
        key: 'ocrType', type: 'segment', label: L('Если текст уже есть', 'Where there is text already'), def: 'skip-text',
        options: [
          { v: 'skip-text', l: L('Пропустить такие страницы', 'Skip those pages') },
          { v: 'force-ocr', l: L('Распознать всё заново', 'Recognize everything again') },
        ],
      },
      { key: 'deskew', type: 'toggle', label: L('Выровнять перекошенные страницы', 'Straighten skewed pages'), def: true },
      { key: 'rotatePages', type: 'toggle', label: L('Повернуть перевёрнутые страницы', 'Turn upside-down pages'), def: true },
    ],
    fixed: { ocrRenderType: 'hocr' },
    action: L('Распознать', 'Recognize'),
    slow: true,
  },
  {
    id: 'repair', cat: 'optimize', icon: 'Wrench', ...PDF, files: 'batch',
    title: L('Починить PDF', 'Repair PDF'),
    desc: L('Попробовать открыть повреждённый файл, который не читается.', 'Try to recover a damaged file that won’t open.'),
    endpoint: 'misc/repair',
    action: L('Починить', 'Repair'),
  },

  // ── To PDF ─────────────────────────────────────────────────────────
  {
    id: 'office-to-pdf', cat: 'topdf', icon: 'FileText', accept: OFFICE, kinds: 'Word, Excel, PowerPoint, ODF, RTF, TXT', files: 'batch',
    title: L('Документ в PDF', 'Document to PDF'),
    desc: L('Word, Excel, PowerPoint, OpenDocument и текст — в PDF.', 'Word, Excel, PowerPoint, OpenDocument and plain text to PDF.'),
    endpoint: 'convert/file/pdf',
    action: MAKE_PDF,
    slow: true,
  },
  {
    id: 'images-to-pdf', cat: 'topdf', icon: 'Images', accept: IMAGES, kinds: 'JPG, PNG, WebP, TIFF', files: 'many', min: 1,
    title: L('Картинки в PDF', 'Images to PDF'),
    desc: L('Собрать фотографии или сканы в один PDF, страница за страницей.', 'Gather photos or scans into one PDF, page by page.'),
    endpoint: 'convert/img/pdf',
    fields: [
      {
        key: 'fitOption', type: 'segment', label: L('Размер страницы', 'Page size'), def: 'fitDocumentToImage',
        options: [
          { v: 'fitDocumentToImage', l: L('Как у картинки', 'Same as the image') },
          { v: 'maintainAspectRatio', l: L('A4, с полями', 'A4 with margins') },
          { v: 'fillPage', l: L('A4, растянуть', 'A4, stretched') },
        ],
      },
      {
        key: 'colorType', type: 'segment', label: COLOR, def: 'color',
        options: [{ v: 'color', l: L('Цветной', 'Color') }, { v: 'greyscale', l: L('Серый', 'Gray') }, { v: 'blackwhite', l: L('Ч/б', 'B/W') }],
      },
      { key: 'autoRotate', type: 'toggle', label: L('Поворачивать под страницу', 'Rotate to fit the page'), def: false },
    ],
    action: MAKE_PDF,
  },
  {
    id: 'ebook-to-pdf', cat: 'topdf', icon: 'BookOpen', accept: '.epub,.fb2,.mobi,.azw3,.azw,.docx', kinds: 'EPUB, FB2, MOBI, AZW3', files: 'batch',
    title: L('Электронная книга в PDF', 'E-book to PDF'),
    desc: L('EPUB, FB2, MOBI и AZW3 — в PDF для печати или читалки.', 'EPUB, FB2, MOBI and AZW3 to PDF, for printing or an e-reader.'),
    endpoint: 'convert/ebook/pdf',
    fields: [
      { key: 'includeTableOfContents', type: 'toggle', label: L('Оглавление', 'Table of contents'), def: true },
      { key: 'includePageNumbers', type: 'toggle', label: L('Номера страниц', 'Page numbers'), def: true },
      { key: 'optimizeForEbook', type: 'toggle', label: L('Для электронной читалки', 'For an e-reader'), def: false },
    ],
    fixed: { embedAllFonts: 'false' },
    action: MAKE_PDF,
    slow: true,
  },
  {
    id: 'email-to-pdf', cat: 'topdf', icon: 'Mail', accept: '.eml,.msg', kinds: 'EML, MSG', files: 'batch',
    title: L('Письмо в PDF', 'Email to PDF'),
    desc: L('Сохранить письмо из почты (EML или MSG) как документ.', 'Save an email (EML or MSG) as a document.'),
    endpoint: 'convert/eml/pdf',
    fields: [
      { key: 'includeAttachments', type: 'toggle', label: L('Вложить файлы из письма', 'Include the email’s attachments'), def: true },
      { key: 'includeAllRecipients', type: 'toggle', label: L('Показать копию и скрытую копию', 'Show CC and BCC'), def: false },
    ],
    action: MAKE_PDF,
  },

  // ── From PDF ───────────────────────────────────────────────────────
  {
    id: 'pdf-to-word', cat: 'frompdf', icon: 'FileType', ...PDF, files: 'batch',
    title: L('PDF в Word', 'PDF to Word'),
    desc: L('Получить редактируемый документ. Сложная вёрстка может поплыть.', 'Get a document you can edit. Complex layouts may shift.'),
    endpoint: 'convert/pdf/word',
    fields: [{ key: 'outputFormat', type: 'segment', label: FORMAT, def: 'docx', options: [{ v: 'docx', l: 'DOCX' }, { v: 'odt', l: 'ODT' }] }],
    action: CONVERT,
    slow: true,
  },
  {
    id: 'pdf-to-excel', cat: 'frompdf', icon: 'Sheet', ...PDF, files: 'batch',
    title: L('PDF в Excel', 'PDF to Excel'),
    desc: L('Вытащить таблицы из PDF в таблицу XLSX.', 'Pull the tables out of a PDF into an XLSX spreadsheet.'),
    endpoint: 'convert/pdf/xlsx',
    fixed: { pageNumbers: 'all' },
    action: CONVERT,
  },
  {
    id: 'pdf-to-ppt', cat: 'frompdf', icon: 'Presentation', ...PDF, files: 'batch',
    title: L('PDF в PowerPoint', 'PDF to PowerPoint'),
    desc: L('Каждая страница станет слайдом.', 'Every page becomes a slide.'),
    endpoint: 'convert/pdf/presentation',
    fields: [{ key: 'outputFormat', type: 'segment', label: FORMAT, def: 'pptx', options: [{ v: 'pptx', l: 'PPTX' }, { v: 'odp', l: 'ODP' }] }],
    action: CONVERT,
    slow: true,
  },
  {
    id: 'pdf-to-images', cat: 'frompdf', icon: 'Image', ...PDF, files: 'one',
    title: L('PDF в картинки', 'PDF to images'),
    desc: L('Каждая страница — отдельная картинка, или все страницы одной лентой.', 'Every page as an image of its own, or all pages as one long strip.'),
    endpoint: 'convert/pdf/img',
    fields: [
      { key: 'imageFormat', type: 'segment', label: FORMAT, def: 'jpg', options: [{ v: 'jpg', l: 'JPG' }, { v: 'png', l: 'PNG' }, { v: 'webp', l: 'WebP' }] },
      {
        key: 'dpi', type: 'segment', label: L('Качество', 'Quality'), def: '150',
        options: [{ v: '96', l: L('Экран', 'Screen') }, { v: '150', l: L('Обычное', 'Normal') }, { v: '300', l: L('Печать', 'Print') }],
      },
      {
        key: 'singleOrMultiple', type: 'segment', label: L('Сколько картинок', 'How many images'), def: 'multiple',
        options: [{ v: 'multiple', l: L('По странице', 'One per page') }, { v: 'single', l: L('Одна лента', 'One strip') }],
      },
      { ...pagesField('pageNumbers', L('Только эти страницы', 'Only these pages'), L('Пусто — все страницы.', 'Leave empty for all pages.')), optional: true },
    ],
    fixed: { colorType: 'color' },
    map: (v) => (v.pageNumbers ? {} : { pageNumbers: 'all' }),
    action: L('Сделать картинки', 'Make images'),
  },
  {
    id: 'pdf-to-text', cat: 'frompdf', icon: 'Type', ...PDF, files: 'batch',
    title: L('PDF в текст', 'PDF to text'),
    desc: L('Только текст: обычный TXT, RTF или Markdown с заголовками.', 'Just the text: plain TXT, RTF, or Markdown with headings.'),
    endpoint: (v) => (v.outputFormat === 'md' ? 'convert/pdf/markdown' : 'convert/pdf/text'),
    fields: [{ key: 'outputFormat', type: 'segment', label: FORMAT, def: 'txt', options: [{ v: 'txt', l: 'TXT' }, { v: 'rtf', l: 'RTF' }, { v: 'md', l: 'Markdown' }] }],
    map: (v) => (v.outputFormat === 'md' ? { outputFormat: null } : {}),
    action: L('Достать текст', 'Extract text'),
  },
  {
    id: 'pdf-to-pdfa', cat: 'frompdf', icon: 'Archive', ...PDF, files: 'batch',
    title: L('PDF в PDF/A', 'PDF to PDF/A'),
    desc: L('Архивный формат, который требуют суды, банки и госорганы.', 'The archival format that courts, banks and government offices ask for.'),
    endpoint: 'convert/pdf/pdfa',
    fields: [{
      key: 'outputFormat', type: 'segment', label: L('Версия', 'Version'), def: 'pdfa-2b',
      options: [{ v: 'pdfa-1', l: 'PDF/A-1b' }, { v: 'pdfa-2b', l: 'PDF/A-2b' }, { v: 'pdfa-3b', l: 'PDF/A-3b' }],
    }],
    action: CONVERT,
    slow: true,
  },

  // ── Layout ─────────────────────────────────────────────────────────
  {
    id: 'watermark', cat: 'edit', icon: 'Stamp', ...PDF, files: 'batch',
    title: L('Водяной знак', 'Watermark'),
    desc: L('Надпись поверх страниц — например «Копия для банка».', 'Text across the pages, such as “Copy for the bank”.'),
    endpoint: 'security/add-watermark',
    fields: [
      { key: 'watermarkText', type: 'text', label: L('Текст', 'Text'), def: L('КОПИЯ', 'COPY'), required: true },
      { key: 'fontSize', type: 'range', label: L('Размер', 'Size'), def: 40, min: 12, max: 120, step: 2 },
      { key: 'opacity', type: 'range', label: L('Заметность', 'Opacity'), def: 0.3, min: 0.1, max: 0.9, step: 0.05, pct: true },
      { key: 'rotation', type: 'segment', label: TILT, def: '45', options: [{ v: '0', l: L('Ровно', 'None') }, { v: '45', l: '45°' }, { v: '-45', l: '−45°' }] },
      { key: 'customColor', type: 'color', label: COLOR, def: '#9aa3b2' },
      { key: 'convertPDFToImage', type: 'toggle', label: L('Запечь в страницы (знак нельзя будет удалить)', 'Bake it into the pages (it can’t be removed)'), def: false },
    ],
    fixed: { watermarkType: 'text', alphabet: 'roman', widthSpacer: '60', heightSpacer: '60' },
    action: L('Поставить знак', 'Add watermark'),
  },
  {
    id: 'metadata', cat: 'edit', icon: 'Tags', ...PDF, files: 'batch',
    title: L('Свойства документа', 'Document properties'),
    desc: L('Поменять название и автора или стереть все сведения о файле.', 'Change the title and author, or wipe everything the file says about itself.'),
    endpoint: 'misc/update-metadata',
    fields: [
      { key: 'deleteAll', type: 'toggle', label: L('Стереть все свойства', 'Wipe all properties'), def: true },
      { key: 'title', type: 'text', label: L('Название', 'Title'), def: '', show: (v) => !v.deleteAll },
      { key: 'author', type: 'text', label: L('Автор', 'Author'), def: '', show: (v) => !v.deleteAll },
      { key: 'subject', type: 'text', label: L('Тема', 'Subject'), def: '', show: (v) => !v.deleteAll },
      { key: 'keywords', type: 'text', label: L('Ключевые слова', 'Keywords'), def: '', show: (v) => !v.deleteAll },
    ],
    fixed: { trapped: 'False' },
    action: L('Сохранить', 'Save'),
  },
  {
    id: 'flatten', cat: 'edit', icon: 'Layers', ...PDF, files: 'batch',
    title: L('Зафиксировать PDF', 'Flatten PDF'),
    desc: L('Сделать заполненную форму и пометки частью страницы, чтобы их нельзя было поменять.', 'Make a filled-in form and annotations part of the page, so they can’t be changed.'),
    endpoint: 'misc/flatten',
    fields: [{
      key: 'flattenOnlyForms', type: 'segment', label: L('Что зафиксировать', 'What to flatten'), def: 'true',
      options: [{ v: 'true', l: L('Только поля формы', 'Form fields only') }, { v: 'false', l: L('Всю страницу картинкой', 'The whole page, as an image') }],
    }],
    action: L('Зафиксировать', 'Flatten'),
  },
  {
    id: 'scan-look', cat: 'edit', icon: 'Printer', ...PDF, files: 'batch',
    title: L('Как будто отсканировано', 'Make it look scanned'),
    desc: L('Сделать PDF похожим на скан: лёгкий наклон, шум, серая бумага.', 'Make a PDF look like a scan: a slight tilt, some noise, gray paper.'),
    endpoint: 'misc/scanner-effect',
    fields: [
      {
        key: 'quality', type: 'segment', label: L('Качество скана', 'Scan quality'), def: 'medium',
        options: [{ v: 'high', l: L('Хорошее', 'Good') }, { v: 'medium', l: L('Среднее', 'Fair') }, { v: 'low', l: L('Плохое', 'Poor') }],
      },
      {
        key: 'rotation', type: 'segment', label: TILT, def: 'slight',
        options: [{ v: 'none', l: L('Нет', 'None') }, { v: 'slight', l: L('Чуть-чуть', 'Slight') }, { v: 'moderate', l: L('Заметный', 'Visible') }],
      },
      { key: 'colorspace', type: 'segment', label: COLOR, def: 'grayscale', options: [{ v: 'grayscale', l: L('Серый', 'Gray') }, { v: 'color', l: L('Цветной', 'Color') }] },
    ],
    action: L('Сделать скан', 'Make the scan'),
  },

  // ── Security and privacy ───────────────────────────────────────────
  {
    id: 'protect', cat: 'security', icon: 'Lock', ...PDF, files: 'batch',
    title: L('Поставить пароль', 'Add a password'),
    desc: L('Без пароля документ не откроется. Можно запретить печать и правки.', 'The document won’t open without it. Printing and editing can be blocked too.'),
    endpoint: 'security/add-password',
    fields: [
      { key: 'password', type: 'password', label: L('Пароль на открытие', 'Password to open'), def: '', required: true, autocomplete: 'new-password' },
      { key: 'preventPrinting', type: 'toggle', label: L('Запретить печать', 'Block printing'), def: false },
      { key: 'preventModify', type: 'toggle', label: L('Запретить правки', 'Block editing'), def: false },
      { key: 'preventExtractContent', type: 'toggle', label: L('Запретить копировать текст', 'Block copying text'), def: false },
    ],
    fixed: { keyLength: '256' },
    action: L('Поставить пароль', 'Add password'),
  },
  {
    id: 'unlock', cat: 'security', icon: 'LockOpen', ...PDF, files: 'batch',
    title: L('Снять пароль', 'Remove password'),
    desc: L('Убрать пароль и ограничения с вашего документа. Пароль нужно знать.', 'Take the password and restrictions off your document. You need to know the password.'),
    endpoint: 'security/remove-password',
    fields: [{
      key: 'password', type: 'password', label: L('Текущий пароль', 'Current password'), def: '', autocomplete: 'current-password',
      hint: L('Если стоят только ограничения на печать или правки, оставьте пустым.', 'If only printing or editing is restricted, leave it empty.'),
    }],
    fixed: { removeAll: 'true' },
    action: L('Снять пароль', 'Remove password'),
  },
  {
    id: 'redact', cat: 'security', icon: 'EyeOff', ...PDF, files: 'batch',
    title: L('Закрасить данные', 'Redact'),
    desc: L('Навсегда скрыть номера паспортов, телефоны и имена перед отправкой.', 'Black out passport numbers, phone numbers and names for good before you send a file.'),
    endpoint: 'security/auto-redact',
    fields: [
      {
        key: 'listOfText', type: 'textarea', label: L('Что закрасить', 'What to black out'), def: '', required: true,
        hint: L('Каждое слово или фразу — с новой строки.', 'One word or phrase per line.'),
        placeholder: L('Иванов\n4510 123456\n+7 900 000-00-00', 'Smith\nAB 123456\n+44 7700 900000'),
      },
      { key: 'wholeWordSearch', type: 'toggle', label: L('Только целые слова', 'Whole words only'), def: false },
      { key: 'caseSensitive', type: 'toggle', label: L('Учитывать регистр', 'Match case'), def: false },
      {
        key: 'convertPDFToImage', type: 'toggle', label: L('Превратить страницы в картинки', 'Turn the pages into images'), def: true,
        hint: L('Так закрашенный текст точно нельзя достать. Текст в файле перестанет выделяться.', 'Then the hidden text surely can’t be recovered. Text in the file will no longer be selectable.'),
      },
    ],
    fixed: { useRegex: 'false', redactColor: '#000000', customPadding: '0' },
    // The engine splits on newlines; normalise stray spaces and blank lines.
    map: (v) => ({ listOfText: String(v.listOfText ?? '').split('\n').map((s) => s.trim()).filter(Boolean).join('\n') }),
    action: L('Закрасить', 'Redact'),
  },
  {
    id: 'sanitize', cat: 'security', icon: 'ShieldCheck', ...PDF, files: 'batch',
    title: L('Очистить от скрытого', 'Remove hidden content'),
    desc: L('Убрать скрипты, вложенные файлы и сведения о том, кто и где делал документ.', 'Strip scripts, embedded files and the details of who made the document, and where.'),
    endpoint: 'security/sanitize-pdf',
    fields: [
      { key: 'removeJavaScript', type: 'toggle', label: L('Скрипты', 'Scripts'), def: true },
      { key: 'removeEmbeddedFiles', type: 'toggle', label: L('Вложенные файлы', 'Embedded files'), def: true },
      { key: 'removeMetadata', type: 'toggle', label: L('Свойства документа', 'Document properties'), def: true },
      { key: 'removeXMPMetadata', type: 'toggle', label: L('Служебные метаданные (XMP)', 'Internal metadata (XMP)'), def: true },
      { key: 'removeLinks', type: 'toggle', label: L('Ссылки', 'Links'), def: false },
    ],
    fixed: { removeFonts: 'false' },
    action: L('Очистить', 'Clean'),
  },

  // ── Images (browser) ───────────────────────────────────────────────
  {
    id: 'image-convert', cat: 'images', icon: 'ImageDown', accept: IMAGES.replace(',.tif,.tiff', ''), kinds: 'JPG, PNG, WebP, GIF, BMP', files: 'batch', mode: 'image',
    title: L('Сжать или переделать картинки', 'Compress or convert images'),
    desc: L('Уменьшить фото и сменить формат: JPG, PNG, WebP.', 'Shrink photos and change their format: JPG, PNG, WebP.'),
    action: L('Сохранить', 'Save'),
  },
]

export const TOOL_BY_ID: Record<string, Tool> = Object.fromEntries(TOOLS.map((t) => [t.id, t]))

// Where a finished PDF can go next (the "continue with this file" row).
export const NEXT_STEPS = ['compress', 'organize', 'merge', 'protect', 'watermark', 'ocr', 'redact', 'pdf-to-word']

// Defaults are read in the language the tool was opened in (the watermark text).
export function defaults(tool: Tool): Values {
  const v: Values = {}
  for (const f of tool.fields || []) {
    const def = f.def
    v[f.key] = Array.isArray(def) ? [...def] : def == null ? '' : typeof def === 'object' ? tx(def) : def
  }
  return v
}

export function visibleFields(tool: Tool, values: Values): Field[] {
  return (tool.fields || []).filter((f) => !f.show || f.show(values))
}

// Returns the first problem with the form, or null.
export function validate(tool: Tool, values: Values, files: File[]): string | null {
  const min = tool.min ?? 1
  if (files.length < min) return min > 1 ? T(`Добавьте хотя бы ${min} файла`, `Add at least ${min} files`) : T('Добавьте файл', 'Add a file')
  for (const f of visibleFields(tool, values)) {
    const val = values[f.key]
    const label = tx(f.label)
    if (f.required && !String(val ?? '').trim()) return T(`Заполните поле «${label}»`, `Fill in “${label}”`)
    if (f.type === 'pages' && !f.optional && !String(val ?? '').trim()) return T(`Выберите страницы: «${label}»`, `Choose the pages: “${label}”`)
    if (f.type === 'pages' && val && !/^[\d,\-\s]+$/.test(String(val))) {
      return T('Номера страниц: только цифры, запятые и дефисы, например 1,3,5-9', 'Page numbers: digits, commas and hyphens only, e.g. 1,3,5-9')
    }
    if (f.type === 'chips' && (!Array.isArray(val) || val.length < (f.min || 0))) return T(`Выберите хотя бы один вариант: «${label}»`, `Pick at least one: “${label}”`)
  }
  return null
}

export function buildForm(tool: Tool, values: Values, files: File[]): FormData {
  const fd = new FormData()
  for (const file of files) fd.append('fileInput', file, file.name)
  const params: Record<string, string | string[] | null | undefined> = {}
  for (const f of visibleFields(tool, values)) {
    if (f.send === false) continue
    const val = values[f.key]
    if (f.type === 'toggle') params[f.key] = val ? 'true' : 'false'
    else if (f.type === 'chips') params[f.key] = Array.isArray(val) ? val : []
    else if (f.type === 'pages') params[f.key] = String(val).replace(/\s+/g, '')
    else params[f.key] = String(val)
  }
  Object.assign(params, tool.fixed || {}, tool.map ? tool.map(values) : {})
  for (const [k, v] of Object.entries(params)) {
    if (v == null || v === '') continue
    if (Array.isArray(v)) v.forEach((x) => fd.append(k, x))
    else fd.append(k, v)
  }
  return fd
}

export function endpointOf(tool: Tool, values: Values): string {
  return typeof tool.endpoint === 'function' ? tool.endpoint(values) : tool.endpoint ?? ''
}
