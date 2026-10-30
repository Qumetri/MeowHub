// Browser-side PDF reading (pdf.js) for page thumbnails, and writing (pdf-lib) for the
// "organize pages" tool. Nothing here talks to the network.
// The legacy build carries polyfills: the modern one needs Uint8Array.toHex & co, which phones
// and Safari older than ~2025 lack (thumbnails silently failed there).
import * as pdfjs from 'pdfjs-dist/legacy/build/pdf.mjs'
import workerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

const docs = new WeakMap()

export async function openPdf(file) {
  if (docs.has(file)) return docs.get(file)
  const p = file.arrayBuffer().then((buf) =>
    pdfjs.getDocument({ data: new Uint8Array(buf), isEvalSupported: false, enableXfa: false }).promise)
  docs.set(file, p)
  p.catch(() => docs.delete(file))
  return p
}

// Renders one page into a fresh canvas at roughly `width` CSS pixels.
export async function renderPage(doc, pageNo, width, rotation = 0) {
  const page = await doc.getPage(pageNo)
  const base = page.getViewport({ scale: 1, rotation: (page.rotate + rotation) % 360 })
  const dpr = Math.min(window.devicePixelRatio || 1, 2)
  const viewport = page.getViewport({ scale: (width / base.width) * dpr, rotation: (page.rotate + rotation) % 360 })
  const canvas = document.createElement('canvas')
  canvas.width = Math.ceil(viewport.width)
  canvas.height = Math.ceil(viewport.height)
  await page.render({ canvas, canvasContext: canvas.getContext('2d'), viewport }).promise
  return canvas
}

export function friendlyPdfError(e) {
  if (e && e.name === 'PasswordException') return 'Файл защищён паролем — сначала снимите его инструментом «Снять пароль».'
  return 'Этот файл не открывается как PDF. Попробуйте «Починить PDF».'
}

// pages: [{ n: original 1-based page number, rot: extra clockwise degrees }]
export async function buildPdf(file, pages) {
  const { PDFDocument, degrees } = await import('pdf-lib')
  const src = await PDFDocument.load(await file.arrayBuffer(), { updateMetadata: false })
  const out = await PDFDocument.create()
  const copied = await out.copyPages(src, pages.map((p) => p.n - 1))
  copied.forEach((page, i) => {
    const rot = pages[i].rot
    if (rot) page.setRotation(degrees((page.getRotation().angle + rot) % 360))
    out.addPage(page)
  })
  const bytes = await out.save()
  return new Blob([bytes], { type: 'application/pdf' })
}
