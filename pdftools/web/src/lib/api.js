// Talks to the engine through Caddy at ./api/v1/<endpoint> (same origin, relative to the page).
// XHR rather than fetch: fetch cannot report upload progress, and big scans take a while to send.

export class ToolError extends Error {
  constructor(message, code) { super(message); this.code = code }
}

function filenameFrom(header, fallback) {
  if (!header) return fallback
  const star = /filename\*\s*=\s*(?:UTF-8'')?([^;]+)/i.exec(header)
  if (star) {
    try { return decodeURIComponent(star[1].trim().replace(/^"|"$/g, '')) } catch { /* fall through */ }
  }
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(header)
  return plain ? plain[1].trim() : fallback
}

// The engine answers errors as JSON ({error} or problem+json {detail}); turn them into one line.
async function errorText(blob, status) {
  let raw = ''
  try { raw = await blob.text() } catch { /* ignore */ }
  let msg = ''
  try {
    const j = JSON.parse(raw)
    msg = j.detail || j.message || j.error || ''
  } catch { msg = '' }
  if (/password/i.test(msg) && /(incorrect|invalid|wrong|encrypted|required)/i.test(msg)) {
    return { text: 'Файл защищён паролем, а пароль не указан или не подошёл.', code: 'password' }
  }
  if (/not available in the font/i.test(msg)) return 'Встроенный шрифт не знает этих букв — используйте латиницу и цифры.'
  if (status === 413) return 'Файл слишком большой: не больше 200 МБ за раз.'
  if (status === 404) return 'Этот инструмент сейчас недоступен на сервере.'
  if (status === 502 || status === 503 || status === 504) return 'Движок не ответил — возможно, он перезапускается. Попробуйте через минуту.'
  const short = msg.replace(/^Job failed:\s*/i, '').split('\n')[0].slice(0, 220)
  return short ? `Не получилось: ${short}` : `Не получилось (код ${status}).`
}

export function runTool(endpoint, formData, { onProgress, signal, fallbackName = 'result' } = {}) {
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
      // 204: the engine found nothing to save (e.g. no tables for PDF → Excel).
      if (xhr.status === 204 || (xhr.status === 200 && xhr.response && xhr.response.size === 0)) {
        reject(new ToolError('Сохранять нечего: в документе не нашлось подходящего содержимого (например, таблиц).'))
      } else if (xhr.status >= 200 && xhr.status < 300) {
        const name = filenameFrom(xhr.getResponseHeader('Content-Disposition'), fallbackName)
        resolve({ blob: xhr.response, name, type: xhr.response.type })
      } else {
        const e = await errorText(xhr.response, xhr.status)
        reject(typeof e === 'string' ? new ToolError(e) : new ToolError(e.text, e.code))
      }
    }
    xhr.onerror = () => reject(new ToolError('Нет связи с сервером. Проверьте интернет и попробуйте снова.'))
    xhr.onabort = () => reject(new DOMException('aborted', 'AbortError'))
    if (signal) signal.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.send(formData)
  })
}
