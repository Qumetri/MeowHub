export function bytes(n) {
  if (n == null) return ''
  if (n < 1024) return `${n} Б`
  const units = ['КБ', 'МБ', 'ГБ']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++ }
  return `${v.toLocaleString('ru-RU', { maximumFractionDigits: v < 10 ? 1 : 0 })} ${units[i]}`
}

// Russian plural: plural(3, ['файл', 'файла', 'файлов'])
export function plural(n, forms) {
  const a = Math.abs(n) % 100
  const b = a % 10
  if (a > 10 && a < 20) return forms[2]
  if (b > 1 && b < 5) return forms[1]
  if (b === 1) return forms[0]
  return forms[2]
}

// {1,2,3,5,7,8} -> "1-3,5,7-8"
export function toRanges(set) {
  const n = [...set].sort((a, b) => a - b)
  const out = []
  for (let i = 0; i < n.length; i++) {
    const start = n[i]
    while (i + 1 < n.length && n[i + 1] === n[i] + 1) i++
    out.push(start === n[i] ? `${start}` : `${start}-${n[i]}`)
  }
  return out.join(',')
}

// "1-3,5" -> Set{1,2,3,5}; silently ignores what it cannot read, clamps to max.
export function fromRanges(text, max = Infinity) {
  const s = new Set()
  for (const part of String(text || '').split(',')) {
    const m = /^\s*(\d+)\s*(?:-\s*(\d+))?\s*$/.exec(part)
    if (!m) continue
    const a = +m[1]
    const b = m[2] ? +m[2] : a
    for (let i = Math.min(a, b); i <= Math.min(Math.max(a, b), max); i++) if (i >= 1) s.add(i)
  }
  return s
}

export function download(blob, name) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 30_000)
}

export function baseName(name) {
  return name.replace(/\.[^.]+$/, '')
}

export function isPdf(file) {
  return file && (file.type === 'application/pdf' || /\.pdf$/i.test(file.name))
}

let seq = 0
// Files get a stable id so lists can be reordered and keyed.
export const wrap = (files) => files.map((file) => ({ id: `f${++seq}`, file }))
