// Browser-only image re-encoding: decode, optionally shrink, encode as JPG/PNG/WebP.
const TYPES = { jpg: 'image/jpeg', png: 'image/png', webp: 'image/webp' }

export async function convertImage(file, { format, quality, maxSide }) {
  const bitmap = await createImageBitmap(file)
  let { width, height } = bitmap
  const longest = Math.max(width, height)
  if (maxSide && longest > maxSide) {
    const k = maxSide / longest
    width = Math.round(width * k)
    height = Math.round(height * k)
  }
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (format === 'jpg') { // JPG has no transparency: put the picture on white
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, width, height)
  }
  ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(bitmap, 0, 0, width, height)
  bitmap.close()
  const blob = await new Promise((res, rej) =>
    canvas.toBlob((b) => (b ? res(b) : rej(new Error('encode'))), TYPES[format], format === 'png' ? undefined : quality))
  const name = `${file.name.replace(/\.[^.]+$/, '')}.${format}`
  return { blob, name, width, height }
}

export async function zipFiles(items) {
  const { zipSync } = await import('fflate')
  const entries = {}
  const used = new Set()
  for (const it of items) {
    let name = it.name
    for (let i = 2; used.has(name); i++) name = it.name.replace(/(\.[^.]+)?$/, ` (${i})$1`)
    used.add(name)
    entries[name] = [new Uint8Array(await it.blob.arrayBuffer()), { level: 0 }]
  }
  return new Blob([zipSync(entries)], { type: 'application/zip' })
}
