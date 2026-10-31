// Browser-only image re-encoding: decode, optionally shrink, encode as JPG/PNG/WebP.
export type ImageFormat = 'jpg' | 'png' | 'webp'
const TYPES: Record<ImageFormat, string> = { jpg: 'image/jpeg', png: 'image/png', webp: 'image/webp' }

export interface ConvertedImage { blob: Blob; name: string; width: number; height: number }

export async function convertImage(file: File, { format, quality, maxSide }: { format: ImageFormat; quality: number; maxSide: number }): Promise<ConvertedImage> {
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
  if (!ctx) throw new Error('no 2d canvas')
  if (format === 'jpg') { // JPG has no transparency: put the picture on white
    ctx.fillStyle = '#fff'
    ctx.fillRect(0, 0, width, height)
  }
  ctx.imageSmoothingQuality = 'high'
  ctx.drawImage(bitmap, 0, 0, width, height)
  bitmap.close()
  const blob = await new Promise<Blob>((res, rej) =>
    canvas.toBlob((b) => (b ? res(b) : rej(new Error('encode'))), TYPES[format], format === 'png' ? undefined : quality))
  const name = `${file.name.replace(/\.[^.]+$/, '')}.${format}`
  return { blob, name, width, height }
}

export async function zipFiles(items: { name: string; blob: Blob }[]): Promise<Blob> {
  const { zipSync } = await import('fflate')
  const entries: import('fflate').Zippable = {}
  const used = new Set<string>()
  for (const it of items) {
    let name = it.name
    for (let i = 2; used.has(name); i++) name = it.name.replace(/(\.[^.]+)?$/, ` (${i})$1`)
    used.add(name)
    entries[name] = [new Uint8Array(await it.blob.arrayBuffer()), { level: 0 }]
  }
  return new Blob([zipSync(entries) as Uint8Array<ArrayBuffer>], { type: 'application/zip' })
}
