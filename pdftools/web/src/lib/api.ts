// Talks to the engine through Caddy at ./api/v1/<endpoint> (same origin, relative to the page).
// XHR rather than fetch: fetch cannot report upload progress, and big scans take a while to send.
import { T } from './i18n'

export type ErrorCode = 'password'

export class ToolError extends Error {
  code?: ErrorCode
  constructor(message: string, code?: ErrorCode) { super(message); this.code = code }
}

export type ProgressEvent =
  | { phase: 'upload'; ratio: number }
  | { phase: 'work' }
  | { phase: 'download'; ratio: number | null }

export interface ToolResult { blob: Blob; name: string; type: string }

function filenameFrom(header: string | null, fallback: string): string {
  if (!header) return fallback
  const star = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i.exec(header)
  if (star?.[1]) {
    try { return decodeURIComponent(star[1].trim().replace(/^"|"$/g, '')) } catch { /* fall through */ }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header)
  return plain?.[1] ? plain[1].trim() : fallback
}

// The engine answers errors as JSON ({error} or problem+json {detail}); turn them into one line.
async function errorText(blob: Blob | null, status: number): Promise<string | { text: string; code: ErrorCode }> {
  let raw = ''
  try { raw = blob ? await blob.text() : '' } catch { /* ignore */ }
  let msg = ''
  try {
    const j = JSON.parse(raw) as { detail?: string; message?: string; error?: string }
    msg = j.detail || j.message || j.error || ''
  } catch { msg = '' }
  if (/password/i.test(msg) && /(incorrect|invalid|wrong|encrypted|required)/i.test(msg)) {
    return { text: T('Файл защищён паролем, а пароль не указан или не подошёл.', 'The file is password-protected, and the password is missing or wrong.'), code: 'password' }
  }
  if (/not available in the font/i.test(msg)) return T('Встроенный шрифт не знает этих букв — используйте латиницу и цифры.', 'The built-in font has no such letters: use Latin letters and digits.')
  if (status === 413) return T('Файл слишком большой: не больше 200 МБ за раз.', 'The file is too big: 200 MB at a time at most.')
  if (status === 404) return T('Этот инструмент сейчас недоступен на сервере.', 'This tool is not available on the server right now.')
  if (status === 502 || status === 503 || status === 504) return T('Движок не ответил — возможно, он перезапускается. Попробуйте через минуту.', 'The engine did not answer; it may be restarting. Try again in a minute.')
  const short = (msg.replace(/^Job failed:\s*/i, '').split('\n')[0] ?? '').slice(0, 220)
  return short ? T(`Не получилось: ${short}`, `That didn’t work: ${short}`) : T(`Не получилось (код ${status}).`, `That didn’t work (code ${status}).`)
}

interface RunOptions {
  onProgress?: (p: ProgressEvent) => void
  signal?: AbortSignal
  fallbackName?: string
}

export function runTool(endpoint: string, formData: FormData, { onProgress, signal, fallbackName = 'result' }: RunOptions = {}): Promise<ToolResult> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `api/v1/${endpoint}`)
    xhr.responseType = 'blob'
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress({ phase: 'upload', ratio: e.loaded / e.total })
    }
    xhr.upload.onload = () => onProgress && onProgress({ phase: 'work' })
    xhr.onprogress = (e) => {
      if (onProgress) onProgress({ phase: 'download', ratio: e.lengthComputable ? e.loaded / e.total : null })
    }
    xhr.onload = async () => {
      const body = xhr.response as Blob | null
      // 204: the engine found nothing to save (e.g. no tables for PDF → Excel).
      if (xhr.status === 204 || (xhr.status === 200 && body && body.size === 0)) {
        reject(new ToolError(T('Сохранять нечего: в документе не нашлось подходящего содержимого (например, таблиц).', 'Nothing to save: the document has nothing of that kind (no tables, for example).')))
      } else if (xhr.status >= 200 && xhr.status < 300 && body) {
        const name = filenameFrom(xhr.getResponseHeader('Content-Disposition'), fallbackName)
        resolve({ blob: body, name, type: body.type })
      } else {
        const e = await errorText(body, xhr.status)
        reject(typeof e === 'string' ? new ToolError(e) : new ToolError(e.text, e.code))
      }
    }
    xhr.onerror = () => reject(new ToolError(T('Нет связи с сервером. Проверьте интернет и попробуйте снова.', 'Can’t reach the server. Check your connection and try again.')))
    xhr.onabort = () => reject(new DOMException('aborted', 'AbortError'))
    if (signal) signal.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.send(formData)
  })
}
