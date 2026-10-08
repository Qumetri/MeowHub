// Fails the build when the ru / en dictionaries disagree: a key missing on
// either side, or a {placeholder} set that differs between the two texts.
// Also flags t('literal.key') calls whose key exists in neither dictionary.
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const { ru, en } = await import(join(root, 'i18n.js'))

const problems = []
const vars = (s) => [...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',')

for (const k of Object.keys(ru)) if (!(k in en)) problems.push(`missing in en: ${k}`)
for (const k of Object.keys(en)) if (!(k in ru)) problems.push(`missing in ru: ${k}`)
for (const k of Object.keys(ru)) {
  if (k in en && vars(ru[k]) !== vars(en[k])) problems.push(`placeholder mismatch: ${k}  ru{${vars(ru[k])}} en{${vars(en[k])}}`)
  if (k in en && ru[k].includes('|') !== en[k].includes('|')) problems.push(`plural forms mismatch: ${k}`)
}

function* walk(dir) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) yield* walk(p)
    else if (/\.(jsx?|mjs)$/.test(f) && f !== 'i18n.js') yield p
  }
}
for (const file of walk(root)) {
  const src = readFileSync(file, 'utf8')
  for (const m of src.matchAll(/(?<![\w.])t\(\s*'([\w.]+)'\s*[,)]/g)) {
    if (!(m[1] in ru) && !(m[1] in en)) problems.push(`unknown key ${m[1]} used in ${file.slice(root.length + 1)}`)
  }
}

if (problems.length) {
  console.error(`i18n check failed (${problems.length}):\n  ` + problems.join('\n  '))
  process.exit(1)
}
console.log(`i18n ok: ${Object.keys(ru).length} keys in ru and en`)
