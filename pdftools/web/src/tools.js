// Every tool on the page. A server tool posts its files to the engine (Stirling-PDF) at
// api/v1/<endpoint>; Caddy only lets through the endpoints listed here (pdftools.caddy) —
// adding a tool means adding its endpoint there too.
//
// files: 'one'   — exactly one file per request
//        'batch' — several files allowed, each is sent on its own (results listed one by one)
//        'many'  — all files go in one request, in the order the person arranged them
// mode:  'server' (default), 'organize' and 'image' run entirely in the browser.

export const CATEGORIES = [
  { id: 'organize', title: 'Страницы и файлы', hue: 'ink' },
  { id: 'optimize', title: 'Сжатие и распознавание', hue: 'moss' },
  { id: 'topdf', title: 'Сделать PDF', hue: 'amber' },
  { id: 'frompdf', title: 'Из PDF в другой формат', hue: 'teal' },
  { id: 'edit', title: 'Оформление', hue: 'rose' },
  { id: 'security', title: 'Защита и приватность', hue: 'brick' },
  { id: 'images', title: 'Картинки', hue: 'sky' },
]

const PDF = { accept: '.pdf,application/pdf', kinds: 'PDF' }
const OFFICE = '.doc,.docx,.odt,.rtf,.txt,.xls,.xlsx,.ods,.csv,.ppt,.pptx,.odp,.odg'
const IMAGES = '.jpg,.jpeg,.png,.webp,.gif,.bmp,.tif,.tiff'

// Page lists use the engine's syntax: "1,3,5-9". The picker writes the same thing.
const pagesField = (key, label, hint) => ({ key, type: 'pages', label, hint, placeholder: 'например 1,3,5-9' })

export const OCR_LANGS = [
  { v: 'rus', l: 'Русский' },
  { v: 'eng', l: 'English' },
  { v: 'ukr', l: 'Українська' },
  { v: 'fin', l: 'Suomi' },
  { v: 'nor', l: 'Norsk' },
  { v: 'swe', l: 'Svenska' },
  { v: 'deu', l: 'Deutsch' },
]

export const TOOLS = [
  // ── Pages and files ────────────────────────────────────────────────
  {
    id: 'merge', cat: 'organize', icon: 'Combine', ...PDF, files: 'many', min: 2,
    title: 'Объединить PDF',
    desc: 'Склеить несколько файлов в один, в том порядке, который вы зададите.',
    endpoint: 'general/merge-pdfs',
    fixed: { sortType: 'orderProvided', removeCertSign: 'true' },
    fields: [{ key: 'generateToc', type: 'toggle', label: 'Добавить оглавление из имён файлов', def: false }],
    action: 'Объединить',
  },
  {
    id: 'organize', cat: 'organize', icon: 'LayoutGrid', ...PDF, files: 'one', mode: 'organize',
    title: 'Упорядочить страницы',
    desc: 'Переставить, повернуть и удалить страницы, глядя на них.',
    action: 'Сохранить PDF',
  },
  {
    id: 'split', cat: 'organize', icon: 'Scissors', ...PDF, files: 'one',
    title: 'Разделить PDF',
    desc: 'Разрезать документ на части: после нужных страниц, по N страниц или по размеру.',
    endpoint: (v) => (v.how === 'after' ? 'general/split-pages' : 'general/split-by-size-or-count'),
    fields: [
      {
        key: 'how', type: 'segment', label: 'Как делить', def: 'after', send: false,
        options: [{ v: 'after', l: 'После страниц' }, { v: 'count', l: 'По N страниц' }, { v: 'size', l: 'По размеру' }],
      },
      { ...pagesField('pageNumbers', 'Резать после страниц', 'Выделите страницы, после которых начинается новая часть.'), show: (v) => v.how === 'after' },
      { key: 'pagesEach', type: 'number', label: 'Страниц в каждой части', def: 5, min: 1, send: false, show: (v) => v.how === 'count' },
      { key: 'sizeEach', type: 'number', label: 'Не больше, МБ', def: 10, min: 1, send: false, show: (v) => v.how === 'size' },
    ],
    map: (v) => (v.how === 'count' ? { splitType: '1', splitValue: String(v.pagesEach) }
      : v.how === 'size' ? { splitType: '0', splitValue: `${v.sizeEach}MB` } : {}),
    action: 'Разделить',
  },
  {
    id: 'remove-pages', cat: 'organize', icon: 'FileMinus', ...PDF, files: 'one',
    title: 'Удалить страницы',
    desc: 'Убрать лишние страницы из документа.',
    endpoint: 'general/remove-pages',
    fields: [pagesField('pageNumbers', 'Какие страницы удалить', 'Нажмите на страницы, которые нужно убрать.')],
    action: 'Удалить страницы',
  },
  {
    id: 'extract', cat: 'organize', icon: 'FileOutput', ...PDF, files: 'one',
    title: 'Извлечь страницы',
    desc: 'Сохранить выбранные страницы отдельным документом.',
    endpoint: 'general/rearrange-pages',
    fixed: { customMode: 'CUSTOM' },
    fields: [pagesField('pageNumbers', 'Какие страницы оставить', 'Страницы попадут в новый файл в порядке номеров.')],
    action: 'Извлечь',
  },
  {
    id: 'rotate', cat: 'organize', icon: 'RotateCw', ...PDF, files: 'batch',
    title: 'Повернуть PDF',
    desc: 'Повернуть все страницы. Отдельные страницы — в «Упорядочить страницы».',
    endpoint: 'general/rotate-pdf',
    fields: [{
      key: 'angle', type: 'segment', label: 'Поворот по часовой', def: '90',
      options: [{ v: '90', l: '90°' }, { v: '180', l: '180°' }, { v: '270', l: '270°' }],
    }],
    action: 'Повернуть',
  },
  {
    id: 'page-numbers', cat: 'organize', icon: 'ListOrdered', ...PDF, files: 'batch',
    title: 'Пронумеровать страницы',
    desc: 'Поставить номера страниц в нужном углу.',
    endpoint: 'misc/add-page-numbers',
    fixed: { fontType: 'helvetica', pageNumbers: 'all' },
    fields: [
      { key: 'position', type: 'position', label: 'Где на странице', def: '8' },
      {
        // The engine's built-in fonts have no Cyrillic, so only Latin patterns are offered.
        key: 'customText', type: 'segment', label: 'Вид', def: '{n}',
        options: [{ v: '{n}', l: '3' }, { v: '{n} / {total}', l: '3 / 12' }, { v: '- {n} -', l: '- 3 -' }],
      },
      { key: 'startingNumber', type: 'number', label: 'Начать с номера', def: 1, min: 0 },
      { key: 'fontSize', type: 'number', label: 'Размер шрифта', def: 11, min: 6, max: 48 },
    ],
    action: 'Пронумеровать',
  },
  {
    id: 'remove-blanks', cat: 'organize', icon: 'FileX', ...PDF, files: 'batch',
    title: 'Убрать пустые страницы',
    desc: 'Найти и удалить пустые листы — частая беда сканов с двух сторон.',
    endpoint: 'misc/remove-blanks',
    fixed: { threshold: '10', whitePercent: '99.9' },
    action: 'Убрать пустые',
  },

  // ── Compression and OCR ────────────────────────────────────────────
  {
    id: 'compress', cat: 'optimize', icon: 'Minimize2', ...PDF, files: 'batch',
    title: 'Сжать PDF',
    desc: 'Уменьшить файл, чтобы он пролез в почту или на госуслуги.',
    endpoint: 'misc/compress-pdf',
    fields: [
      {
        key: 'optimizeLevel', type: 'segment', label: 'Насколько сильно', def: '5',
        options: [
          { v: '3', l: 'Бережно', hint: 'Без потери качества, выигрыш небольшой' },
          { v: '5', l: 'Обычно', hint: 'Картинки чуть мягче, файл заметно меньше' },
          { v: '8', l: 'Сильно', hint: 'Максимум экономии, картинки заметно хуже' },
        ],
      },
      { key: 'grayscale', type: 'toggle', label: 'Сделать чёрно-белым', def: false },
    ],
    fixed: { linearize: 'false', normalize: 'false' },
    action: 'Сжать',
    compare: true,
  },
  {
    id: 'ocr', cat: 'optimize', icon: 'ScanText', ...PDF, files: 'batch',
    title: 'Распознать текст (OCR)',
    desc: 'Превратить скан в PDF, где текст можно искать и копировать.',
    endpoint: 'misc/ocr-pdf',
    fields: [
      { key: 'languages', type: 'chips', label: 'Языки документа', def: ['rus', 'eng'], options: OCR_LANGS, min: 1 },
      {
        key: 'ocrType', type: 'segment', label: 'Если текст уже есть', def: 'skip-text',
        options: [
          { v: 'skip-text', l: 'Пропустить такие страницы' },
          { v: 'force-ocr', l: 'Распознать всё заново' },
        ],
      },
      { key: 'deskew', type: 'toggle', label: 'Выровнять перекошенные страницы', def: true },
      { key: 'rotatePages', type: 'toggle', label: 'Повернуть перевёрнутые страницы', def: true },
    ],
    fixed: { ocrRenderType: 'hocr' },
    action: 'Распознать',
    slow: true,
  },
  {
    id: 'repair', cat: 'optimize', icon: 'Wrench', ...PDF, files: 'batch',
    title: 'Починить PDF',
    desc: 'Попробовать открыть повреждённый файл, который не читается.',
    endpoint: 'misc/repair',
    action: 'Починить',
  },

  // ── To PDF ─────────────────────────────────────────────────────────
  {
    id: 'office-to-pdf', cat: 'topdf', icon: 'FileText', accept: OFFICE, kinds: 'Word, Excel, PowerPoint, ODF, RTF, TXT', files: 'batch',
    title: 'Документ в PDF',
    desc: 'Word, Excel, PowerPoint, OpenDocument и текст — в PDF.',
    endpoint: 'convert/file/pdf',
    action: 'Сделать PDF',
    slow: true,
  },
  {
    id: 'images-to-pdf', cat: 'topdf', icon: 'Images', accept: IMAGES, kinds: 'JPG, PNG, WebP, TIFF', files: 'many', min: 1,
    title: 'Картинки в PDF',
    desc: 'Собрать фотографии или сканы в один PDF, страница за страницей.',
    endpoint: 'convert/img/pdf',
    fields: [
      {
        key: 'fitOption', type: 'segment', label: 'Размер страницы', def: 'fitDocumentToImage',
        options: [{ v: 'fitDocumentToImage', l: 'Как у картинки' }, { v: 'maintainAspectRatio', l: 'A4, с полями' }, { v: 'fillPage', l: 'A4, растянуть' }],
      },
      {
        key: 'colorType', type: 'segment', label: 'Цвет', def: 'color',
        options: [{ v: 'color', l: 'Цветной' }, { v: 'greyscale', l: 'Серый' }, { v: 'blackwhite', l: 'Ч/б' }],
      },
      { key: 'autoRotate', type: 'toggle', label: 'Поворачивать под страницу', def: false },
    ],
    action: 'Сделать PDF',
  },
  {
    id: 'ebook-to-pdf', cat: 'topdf', icon: 'BookOpen', accept: '.epub,.fb2,.mobi,.azw3,.azw,.docx', kinds: 'EPUB, FB2, MOBI, AZW3', files: 'batch',
    title: 'Электронная книга в PDF',
    desc: 'EPUB, FB2, MOBI и AZW3 — в PDF для печати или читалки.',
    endpoint: 'convert/ebook/pdf',
    fields: [
      { key: 'includeTableOfContents', type: 'toggle', label: 'Оглавление', def: true },
      { key: 'includePageNumbers', type: 'toggle', label: 'Номера страниц', def: true },
      { key: 'optimizeForEbook', type: 'toggle', label: 'Для электронной читалки', def: false },
    ],
    fixed: { embedAllFonts: 'false' },
    action: 'Сделать PDF',
    slow: true,
  },
  {
    id: 'email-to-pdf', cat: 'topdf', icon: 'Mail', accept: '.eml,.msg', kinds: 'EML, MSG', files: 'batch',
    title: 'Письмо в PDF',
    desc: 'Сохранить письмо из почты (EML или MSG) как документ.',
    endpoint: 'convert/eml/pdf',
    fields: [
      { key: 'includeAttachments', type: 'toggle', label: 'Вложить файлы из письма', def: true },
      { key: 'includeAllRecipients', type: 'toggle', label: 'Показать копию и скрытую копию', def: false },
    ],
    action: 'Сделать PDF',
  },

  // ── From PDF ───────────────────────────────────────────────────────
  {
    id: 'pdf-to-word', cat: 'frompdf', icon: 'FileType', ...PDF, files: 'batch',
    title: 'PDF в Word',
    desc: 'Получить редактируемый документ. Сложная вёрстка может поплыть.',
    endpoint: 'convert/pdf/word',
    fields: [{ key: 'outputFormat', type: 'segment', label: 'Формат', def: 'docx', options: [{ v: 'docx', l: 'DOCX' }, { v: 'odt', l: 'ODT' }] }],
    action: 'Конвертировать',
    slow: true,
  },
  {
    id: 'pdf-to-excel', cat: 'frompdf', icon: 'Sheet', ...PDF, files: 'batch',
    title: 'PDF в Excel',
    desc: 'Вытащить таблицы из PDF в таблицу XLSX.',
    endpoint: 'convert/pdf/xlsx',
    fixed: { pageNumbers: 'all' },
    action: 'Конвертировать',
  },
  {
    id: 'pdf-to-ppt', cat: 'frompdf', icon: 'Presentation', ...PDF, files: 'batch',
    title: 'PDF в PowerPoint',
    desc: 'Каждая страница станет слайдом.',
    endpoint: 'convert/pdf/presentation',
    fields: [{ key: 'outputFormat', type: 'segment', label: 'Формат', def: 'pptx', options: [{ v: 'pptx', l: 'PPTX' }, { v: 'odp', l: 'ODP' }] }],
    action: 'Конвертировать',
    slow: true,
  },
  {
    id: 'pdf-to-images', cat: 'frompdf', icon: 'Image', ...PDF, files: 'one',
    title: 'PDF в картинки',
    desc: 'Каждая страница — отдельная картинка, или все страницы одной лентой.',
    endpoint: 'convert/pdf/img',
    fields: [
      { key: 'imageFormat', type: 'segment', label: 'Формат', def: 'jpg', options: [{ v: 'jpg', l: 'JPG' }, { v: 'png', l: 'PNG' }, { v: 'webp', l: 'WebP' }] },
      {
        key: 'dpi', type: 'segment', label: 'Качество', def: '150',
        options: [{ v: '96', l: 'Экран' }, { v: '150', l: 'Обычное' }, { v: '300', l: 'Печать' }],
      },
      { key: 'singleOrMultiple', type: 'segment', label: 'Сколько картинок', def: 'multiple', options: [{ v: 'multiple', l: 'По странице' }, { v: 'single', l: 'Одна лента' }] },
      { ...pagesField('pageNumbers', 'Только эти страницы', 'Пусто — все страницы.'), optional: true },
    ],
    fixed: { colorType: 'color' },
    map: (v) => (v.pageNumbers ? {} : { pageNumbers: 'all' }),
    action: 'Сделать картинки',
  },
  {
    id: 'pdf-to-text', cat: 'frompdf', icon: 'Type', ...PDF, files: 'batch',
    title: 'PDF в текст',
    desc: 'Только текст: обычный TXT, RTF или Markdown с заголовками.',
    endpoint: (v) => (v.outputFormat === 'md' ? 'convert/pdf/markdown' : 'convert/pdf/text'),
    fields: [{ key: 'outputFormat', type: 'segment', label: 'Формат', def: 'txt', options: [{ v: 'txt', l: 'TXT' }, { v: 'rtf', l: 'RTF' }, { v: 'md', l: 'Markdown' }] }],
    map: (v) => (v.outputFormat === 'md' ? { outputFormat: null } : {}),
    action: 'Достать текст',
  },
  {
    id: 'pdf-to-pdfa', cat: 'frompdf', icon: 'Archive', ...PDF, files: 'batch',
    title: 'PDF в PDF/A',
    desc: 'Архивный формат, который требуют суды, банки и госорганы.',
    endpoint: 'convert/pdf/pdfa',
    fields: [{
      key: 'outputFormat', type: 'segment', label: 'Версия', def: 'pdfa-2b',
      options: [{ v: 'pdfa-1', l: 'PDF/A-1b' }, { v: 'pdfa-2b', l: 'PDF/A-2b' }, { v: 'pdfa-3b', l: 'PDF/A-3b' }],
    }],
    action: 'Конвертировать',
    slow: true,
  },

  // ── Layout ─────────────────────────────────────────────────────────
  {
    id: 'watermark', cat: 'edit', icon: 'Stamp', ...PDF, files: 'batch',
    title: 'Водяной знак',
    desc: 'Надпись поверх страниц — например «Копия для банка».',
    endpoint: 'security/add-watermark',
    fields: [
      { key: 'watermarkText', type: 'text', label: 'Текст', def: 'КОПИЯ', required: true },
      { key: 'fontSize', type: 'range', label: 'Размер', def: 40, min: 12, max: 120, step: 2 },
      { key: 'opacity', type: 'range', label: 'Заметность', def: 0.3, min: 0.1, max: 0.9, step: 0.05, pct: true },
      { key: 'rotation', type: 'segment', label: 'Наклон', def: '45', options: [{ v: '0', l: 'Ровно' }, { v: '45', l: '45°' }, { v: '-45', l: '−45°' }] },
      { key: 'customColor', type: 'color', label: 'Цвет', def: '#9aa3b2' },
      { key: 'convertPDFToImage', type: 'toggle', label: 'Запечь в страницы (знак нельзя будет удалить)', def: false },
    ],
    fixed: { watermarkType: 'text', alphabet: 'roman', widthSpacer: '60', heightSpacer: '60' },
    action: 'Поставить знак',
  },
  {
    id: 'metadata', cat: 'edit', icon: 'Tags', ...PDF, files: 'batch',
    title: 'Свойства документа',
    desc: 'Поменять название и автора или стереть все сведения о файле.',
    endpoint: 'misc/update-metadata',
    fields: [
      { key: 'deleteAll', type: 'toggle', label: 'Стереть все свойства', def: true },
      { key: 'title', type: 'text', label: 'Название', def: '', show: (v) => !v.deleteAll },
      { key: 'author', type: 'text', label: 'Автор', def: '', show: (v) => !v.deleteAll },
      { key: 'subject', type: 'text', label: 'Тема', def: '', show: (v) => !v.deleteAll },
      { key: 'keywords', type: 'text', label: 'Ключевые слова', def: '', show: (v) => !v.deleteAll },
    ],
    fixed: { trapped: 'False' },
    action: 'Сохранить',
  },
  {
    id: 'flatten', cat: 'edit', icon: 'Layers', ...PDF, files: 'batch',
    title: 'Зафиксировать PDF',
    desc: 'Сделать заполненную форму и пометки частью страницы, чтобы их нельзя было поменять.',
    endpoint: 'misc/flatten',
    fields: [{
      key: 'flattenOnlyForms', type: 'segment', label: 'Что зафиксировать', def: 'true',
      options: [{ v: 'true', l: 'Только поля формы' }, { v: 'false', l: 'Всю страницу картинкой' }],
    }],
    action: 'Зафиксировать',
  },
  {
    id: 'scan-look', cat: 'edit', icon: 'Printer', ...PDF, files: 'batch',
    title: 'Как будто отсканировано',
    desc: 'Сделать PDF похожим на скан: лёгкий наклон, шум, серая бумага.',
    endpoint: 'misc/scanner-effect',
    fields: [
      { key: 'quality', type: 'segment', label: 'Качество скана', def: 'medium', options: [{ v: 'high', l: 'Хорошее' }, { v: 'medium', l: 'Среднее' }, { v: 'low', l: 'Плохое' }] },
      { key: 'rotation', type: 'segment', label: 'Наклон', def: 'slight', options: [{ v: 'none', l: 'Нет' }, { v: 'slight', l: 'Чуть-чуть' }, { v: 'moderate', l: 'Заметный' }] },
      { key: 'colorspace', type: 'segment', label: 'Цвет', def: 'grayscale', options: [{ v: 'grayscale', l: 'Серый' }, { v: 'color', l: 'Цветной' }] },
    ],
    action: 'Сделать скан',
  },

  // ── Security and privacy ───────────────────────────────────────────
  {
    id: 'protect', cat: 'security', icon: 'Lock', ...PDF, files: 'batch',
    title: 'Поставить пароль',
    desc: 'Без пароля документ не откроется. Можно запретить печать и правки.',
    endpoint: 'security/add-password',
    fields: [
      { key: 'password', type: 'password', label: 'Пароль на открытие', def: '', required: true, autocomplete: 'new-password' },
      { key: 'preventPrinting', type: 'toggle', label: 'Запретить печать', def: false },
      { key: 'preventModify', type: 'toggle', label: 'Запретить правки', def: false },
      { key: 'preventExtractContent', type: 'toggle', label: 'Запретить копировать текст', def: false },
    ],
    fixed: { keyLength: '256' },
    action: 'Поставить пароль',
  },
  {
    id: 'unlock', cat: 'security', icon: 'LockOpen', ...PDF, files: 'batch',
    title: 'Снять пароль',
    desc: 'Убрать пароль и ограничения с вашего документа. Пароль нужно знать.',
    endpoint: 'security/remove-password',
    fields: [{ key: 'password', type: 'password', label: 'Текущий пароль', def: '', autocomplete: 'current-password', hint: 'Если стоят только ограничения на печать или правки, оставьте пустым.' }],
    fixed: { removeAll: 'true' },
    action: 'Снять пароль',
  },
  {
    id: 'redact', cat: 'security', icon: 'EyeOff', ...PDF, files: 'batch',
    title: 'Закрасить данные',
    desc: 'Навсегда скрыть номера паспортов, телефоны и имена перед отправкой.',
    endpoint: 'security/auto-redact',
    fields: [
      { key: 'listOfText', type: 'textarea', label: 'Что закрасить', def: '', required: true, hint: 'Каждое слово или фразу — с новой строки.', placeholder: 'Иванов\n4510 123456\n+7 900 000-00-00' },
      { key: 'wholeWordSearch', type: 'toggle', label: 'Только целые слова', def: false },
      { key: 'caseSensitive', type: 'toggle', label: 'Учитывать регистр', def: false },
      { key: 'convertPDFToImage', type: 'toggle', label: 'Превратить страницы в картинки', def: true, hint: 'Так закрашенный текст точно нельзя достать. Текст в файле перестанет выделяться.' },
    ],
    fixed: { useRegex: 'false', redactColor: '#000000', customPadding: '0' },
    // The engine splits on newlines; normalise stray spaces and blank lines.
    map: (v) => ({ listOfText: v.listOfText.split('\n').map((s) => s.trim()).filter(Boolean).join('\n') }),
    action: 'Закрасить',
  },
  {
    id: 'sanitize', cat: 'security', icon: 'ShieldCheck', ...PDF, files: 'batch',
    title: 'Очистить от скрытого',
    desc: 'Убрать скрипты, вложенные файлы и сведения о том, кто и где делал документ.',
    endpoint: 'security/sanitize-pdf',
    fields: [
      { key: 'removeJavaScript', type: 'toggle', label: 'Скрипты', def: true },
      { key: 'removeEmbeddedFiles', type: 'toggle', label: 'Вложенные файлы', def: true },
      { key: 'removeMetadata', type: 'toggle', label: 'Свойства документа', def: true },
      { key: 'removeXMPMetadata', type: 'toggle', label: 'Служебные метаданные (XMP)', def: true },
      { key: 'removeLinks', type: 'toggle', label: 'Ссылки', def: false },
    ],
    fixed: { removeFonts: 'false' },
    action: 'Очистить',
  },

  // ── Images (browser) ───────────────────────────────────────────────
  {
    id: 'image-convert', cat: 'images', icon: 'ImageDown', accept: IMAGES.replace(',.tif,.tiff', ''), kinds: 'JPG, PNG, WebP, GIF, BMP', files: 'batch', mode: 'image',
    title: 'Сжать или переделать картинки',
    desc: 'Уменьшить фото и сменить формат: JPG, PNG, WebP.',
    action: 'Сохранить',
  },
]

export const TOOL_BY_ID = Object.fromEntries(TOOLS.map((t) => [t.id, t]))

// Where a finished PDF can go next (the "continue with this file" row).
export const NEXT_STEPS = ['compress', 'organize', 'merge', 'protect', 'watermark', 'ocr', 'redact', 'pdf-to-word']

export function defaults(tool) {
  const v = {}
  for (const f of tool.fields || []) v[f.key] = Array.isArray(f.def) ? [...f.def] : f.def ?? ''
  return v
}

export function visibleFields(tool, values) {
  return (tool.fields || []).filter((f) => !f.show || f.show(values))
}

// Returns the first problem with the form, or null.
export function validate(tool, values, files) {
  const min = tool.min ?? 1
  if (files.length < min) return min > 1 ? `Добавьте хотя бы ${min} файла` : 'Добавьте файл'
  for (const f of visibleFields(tool, values)) {
    const val = values[f.key]
    if (f.required && !String(val ?? '').trim()) return `Заполните поле «${f.label}»`
    if (f.type === 'pages' && !f.optional && !String(val ?? '').trim()) return `Выберите страницы: «${f.label}»`
    if (f.type === 'pages' && val && !/^[\d,\-\s]+$/.test(val)) return 'Номера страниц: только цифры, запятые и дефисы, например 1,3,5-9'
    if (f.type === 'chips' && (!val || val.length < (f.min || 0))) return `Выберите хотя бы один вариант: «${f.label}»`
  }
  return null
}

export function buildForm(tool, values, files) {
  const fd = new FormData()
  for (const file of files) fd.append('fileInput', file, file.name)
  const params = {}
  for (const f of visibleFields(tool, values)) {
    if (f.send === false) continue
    const val = values[f.key]
    if (f.type === 'toggle') params[f.key] = val ? 'true' : 'false'
    else if (f.type === 'chips') params[f.key] = val
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

export function endpointOf(tool, values) {
  return typeof tool.endpoint === 'function' ? tool.endpoint(values) : tool.endpoint
}
