import { useCallback, useEffect, useRef, useState } from 'react'
import Icon from '../icons'
import { Button, Empty, ErrorBox, Pill, Skeleton, toast, useAuthedImage } from '../ui'
import { api, inPreview } from '../api'
import { getLang, t } from '../i18n'
import { confirmDialog, haptic, initData, isTg, saveFile } from '../tg'
import { fmtBytes, fmtDur, plural } from '../util'
import { Title } from './shared'
import type { ApiError } from '../api'
import type { BookStatus, BookVoice, BooksView, Lang, MemberBook, Quota as QuotaInfo, Tone } from '../types'

// Audiobooks (beta). The flow per book is a short sequence, shown as steps on each card:
// upload → pick a voice → wait in line → listen. Everything slow is explained in place.
const BUSY = new Set<BookStatus>(['preparing', 'queued', 'narrating', 'assembling'])
const STEPS = ['upload', 'voice', 'queue', 'listen']
const STEP_AT: Record<BookStatus, number> = { preparing: 0, ready: 1, cancelled: 1, error: 1, queued: 2, narrating: 2, assembling: 2, done: 4 }
const stepOf = (st: BookStatus): number => STEP_AT[st] ?? 0
const PILL: Record<BookStatus, Tone> = { preparing: 'mute', ready: 'accent', queued: 'mute', narrating: 'accent', assembling: 'accent', done: 'ok', error: 'bad', cancelled: 'mute' }
const booksWord = (n: number): string => plural(n, t('books.unit_books').split('|'))

// GET /api/books every 3 s while something is moving, else every 30 s; paused while hidden.
function useBooks() {
  const [st, setSt] = useState<{ data: BooksView | null; error: ApiError | null }>({ data: null, error: null })
  const kick = useRef<() => void>(() => {})
  useEffect(() => {
    let stop = false, timer = 0
    async function tick(): Promise<void> {
      clearTimeout(timer)
      if (stop || document.hidden) return
      let busy = false
      try {
        const data = await api<BooksView>('books')
        busy = data.books.some((b) => BUSY.has(b.status))
        if (!stop) setSt({ data, error: null })
      } catch (error) {
        if (!stop) setSt((s) => ({ data: s.data, error: error as ApiError }))
      }
      if (!stop) timer = window.setTimeout(tick, busy ? 3000 : 30000)
    }
    const vis = () => { if (!document.hidden) void tick() }
    kick.current = () => { void tick() }
    document.addEventListener('visibilitychange', vis)
    void tick()
    return () => { stop = true; clearTimeout(timer); document.removeEventListener('visibilitychange', vis) }
  }, [])
  return { ...st, refresh: () => kick.current() }
}

// Voice samples: fetched with the auth header (an <audio src> can't send one) and played
// from a blob; one shared player so a new sample stops the previous.
export interface SampleState { key: string | null; loading: boolean }
const sampleCache = new Map<string, string>()
function useSample(): [SampleState, (key: string, lang: Lang) => Promise<void>] {
  const audio = useRef<HTMLAudioElement | null>(null)
  const [st, setSt] = useState<SampleState>({ key: null, loading: false })
  useEffect(() => {
    const a = new Audio()
    audio.current = a
    a.onended = () => setSt({ key: null, loading: false })
    return () => { a.pause(); a.src = '' }
  }, [])
  const toggle = async (key: string, lang: Lang) => {
    const a = audio.current
    if (!a) return
    if (st.key === key) { a.pause(); setSt({ key: null, loading: false }); return }
    haptic.impact('light')
    a.pause()
    setSt({ key, loading: true })
    try {
      let url = sampleCache.get(key)
      if (!url) {
        if (inPreview()) throw new Error(t('books.preview_off'))
        const r = await fetch(`./api/books/preview?voice=${encodeURIComponent(key)}&lang=${lang}`,
          { headers: isTg ? { 'X-Tg-Init-Data': initData, 'X-Lang': getLang() } : { 'X-Lang': getLang() } })
        if (!r.ok) throw new Error(t('books.sample_fail'))
        url = URL.createObjectURL(await r.blob())
        sampleCache.set(key, url)
      }
      a.src = url
      await a.play()
      setSt({ key, loading: false })
    } catch (e) {
      setSt({ key: null, loading: false })
      toast((e as Error).message || t('books.sample_fail'), 'bad')
    }
  }
  return [st, toggle]
}

// Upload with progress (fetch can't report upload progress; XHR can).
function uploadBook(file: File, onProgress: (p: number) => void): Promise<MemberBook | null> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest()
    x.open('POST', './api/books/upload')
    if (isTg) x.setRequestHeader('X-Tg-Init-Data', initData)
    x.setRequestHeader('X-Lang', getLang())
    x.setRequestHeader('X-Filename', encodeURIComponent(file.name))
    const type = ['application/epub+zip', 'application/pdf'].includes(file.type) ? file.type : 'application/octet-stream'
    x.setRequestHeader('Content-Type', type)
    x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total) }
    x.onload = () => {
      let d: (MemberBook & { message?: string }) | null = null
      try { d = JSON.parse(x.responseText) } catch { /* not JSON */ }
      if (x.status >= 200 && x.status < 300) resolve(d)
      else reject(new Error(d?.message || t('err.generic')))
    }
    x.onerror = () => reject(new Error(t('err.network')))
    x.send(file)
  })
}

function Quota({ quota }: { quota: QuotaInfo & { per_user: number } }) {
  if (quota.unlimited) return <p className="bk-quota">{t('books.quota_owner')}</p>
  return (
    <div className="bk-quota" aria-label={t('books.quota_left', { n: quota.left, total: quota.per_user })}>
      <span className="bk-quota__dots" aria-hidden="true">
        {Array.from({ length: quota.per_user }, (_, i) => <i key={i} className={i < quota.used ? 'used' : ''} />)}
      </span>
      <span>{t('books.quota_left', { n: quota.left, total: quota.per_user })}</span>
    </div>
  )
}

function Steps({ status }: { status: BookStatus }) {
  const at = stepOf(status)
  return (
    <ol className="bk-steps" aria-label={t('books.steps')}>
      {STEPS.map((s, i) => (
        <li key={s} className={i < at ? 'done' : i === at ? 'now' : ''} aria-current={i === at ? 'step' : undefined}>
          <span>{t('books.step_' + s)}</span>
        </li>
      ))}
    </ol>
  )
}

function Cover({ book }: { book: MemberBook }) {
  const img = useAuthedImage(book.has_cover ? `./api/books/${book.id}/cover` : null)
  return (
    <span className="bk-cover" aria-hidden="true">
      {img.src ? <img src={img.src} alt="" /> : <Icon name="book" size={22} />}
    </span>
  )
}

interface VoicePickProps { book: MemberBook; voices: BookVoice[]; sample: SampleState; onSample: (key: string, lang: Lang) => void; value: string; onChange: (key: string) => void }
function VoicePick({ book, voices, sample, onSample, value, onChange }: VoicePickProps) {
  return (
    <div className="bk-voices" role="radiogroup" aria-label={t('books.voice')}>
      {voices.map((v) => {
        const playing = sample.key === v.key
        return (
          <div key={v.key} className={'bk-voice' + (value === v.key ? ' on' : '')}>
            <button type="button" className="iconbtn iconbtn--in" aria-label={t(playing ? 'books.sample_stop' : 'books.sample', { name: v.name })}
              onClick={() => onSample(v.key, book.lang)}>
              {playing && sample.loading ? <span className="spin" /> : <Icon name={playing ? 'pause' : 'play'} size={18} />}
            </button>
            <label>
              <input type="radio" name={'voice-' + book.id} checked={value === v.key} onChange={() => { haptic.select(); onChange(v.key) }} />
              <span className="bk-voice__n">{v.name}</span>
              <span className="bk-voice__m">{t(v.gender === 'f' ? 'books.female' : 'books.male')}{v.accent ? ', ' + t('books.accent_' + v.accent) : ''}</span>
            </label>
          </div>
        )
      })}
    </div>
  )
}

interface BookCardProps {
  book: MemberBook
  voices: BookVoice[]
  queue: BooksView['queue']
  sample: SampleState
  onSample: (key: string, lang: Lang) => void
  onChanged: () => void
  limitLeft: number
}
function BookCard({ book, voices, queue, sample, onSample, onChanged, limitLeft }: BookCardProps) {
  const [voice, setVoice] = useState(book.voice || voices[0]?.key || '')
  const [busy, setBusy] = useState('')
  const audioUrl = useRef(book.audio_url)      // keep the first signed URL: a new src would restart playback
  if (!audioUrl.current && book.audio_url) audioUrl.current = book.audio_url
  const engine = voice.split(':')[0] ?? ''
  const canNarrate = ['ready', 'cancelled', 'error'].includes(book.status)

  async function run(name: string, fn: () => Promise<void>) {
    if (busy) return
    haptic.impact('light'); setBusy(name)
    try { await fn() } catch (e) { haptic.notify('error'); toast((e as Error).message, 'bad') } finally { setBusy('') }
  }
  const narrate = () => run('narrate', async () => {
    await api(`books/${book.id}/narrate`, { method: 'POST', body: { voice } })
    haptic.notify('success'); toast(t('books.queued_toast')); onChanged()
  })
  const cancel = () => run('cancel', async () => { await api(`books/${book.id}/cancel`, { method: 'POST' }); onChanged() })
  const del = () => run('delete', async () => {
    const msg = book.status === 'done' ? t('books.delete_done_q') : t('books.delete_q')
    if (!(await confirmDialog(msg))) return
    await api(`books/${book.id}/delete`, { method: 'POST' }); onChanged()
  })

  const w = book.wait
  return (
    <article className="bk" aria-busy={BUSY.has(book.status) || undefined}>
      <div className="bk__top">
        <Cover book={book} />
        <div className="bk__body">
          <h4 className="bk__title">{book.title || t('books.untitled')}</h4>
          {book.author && <p className="bk__author">{book.author}</p>}
          <p className="bk__meta">
            {book.status === 'done' && book.seconds
              ? <>{fmtDur(book.seconds)}{book.size ? <> · {fmtBytes(book.size)}</> : null}</>
              : book.listen_s > 0 ? <>{t('books.listen', { t: fmtDur(book.listen_s) })}</> : null}
          </p>
        </div>
        <Pill tone={PILL[book.status] || 'mute'}>{t('books.st.' + book.status)}</Pill>
      </div>

      <Steps status={book.status} />

      {book.status === 'preparing' && (
        <p className="bk__note" role="status"><span className="spin" /> {t('books.preparing')}</p>
      )}

      {canNarrate && (
        <div className="bk__pick">
          {book.status === 'error' && <p className="bk__err">{book.error}</p>}
          {voices.length === 0
            ? <p className="bk__err">{t('books.no_voices')}</p>
            : <>
                <p className="form__l">{t('books.voice')}</p>
                <VoicePick book={book} voices={voices} sample={sample} onSample={onSample} value={voice} onChange={setVoice} />
                <p className="bk__note">
                  {book.takes?.[engine] ? t('books.takes', { t: fmtDur(book.takes[engine]) }) : null}
                  {queue && queue.books > 0 ? ' ' + t('books.line_now', { n: queue.books, books: booksWord(queue.books), t: fmtDur(queue.wait) }) : ''}
                </p>
                <div className="bk__act">
                  <Button kind="primary" icon="mic" loading={busy === 'narrate'} disabled={!voice || limitLeft <= 0} onClick={narrate}>{t('books.narrate')}</Button>
                  <Button kind="plain" icon="trash" loading={busy === 'delete'} onClick={del}>{t('books.delete')}</Button>
                </div>
                {limitLeft <= 0 && <p className="bk__err">{t('books.limit_reached')}</p>}
              </>}
        </div>
      )}

      {book.status === 'queued' && (
        <div className="bk__wait" role="status">
          <Icon name="clock" size={20} />
          <div>
            {w ? <>
              <p><b>{w.ahead > 0 ? t('books.ahead', { n: w.ahead, books: booksWord(w.ahead) }) : t('books.next')}</b></p>
              <p>{w.ahead > 0 ? t('books.starts_in', { t: fmtDur(w.starts_in) }) + ' ' : ''}{t('books.ready_in', { t: fmtDur(w.starts_in + w.takes) })}</p>
            </> : <p>{t('books.queued')}</p>}
            <p className="bk__hint">{t('books.we_notify')}</p>
          </div>
        </div>
      )}

      {(book.status === 'narrating' || book.status === 'assembling') && (
        <div className="bk__prog">
          <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={book.percent ?? 0}>
            <i style={{ width: `${Math.max(2, book.percent ?? 0)}%` }} />
          </div>
          <div className="job__pl">
            <span>{book.status === 'assembling' ? t('books.assembling') : `${Math.floor(book.percent ?? 0)}%`}</span>
            {book.eta != null && book.status === 'narrating' && <span>{t('books.ready_in', { t: fmtDur(book.eta) })}</span>}
          </div>
          <p className="bk__hint">{t('books.we_notify')}</p>
        </div>
      )}

      {(book.status === 'queued' || book.status === 'narrating') && (
        <div className="bk__act"><Button kind="danger" size="s" icon="x" loading={busy === 'cancel'} onClick={cancel}>{t('books.cancel')}</Button></div>
      )}

      {book.status === 'done' && (
        <div className="bk__done">
          {audioUrl.current && <audio className="bk__audio" controls preload="none" src={audioUrl.current} />}
          <div className="bk__act">
            <Button kind="primary" size="s" icon="download" onClick={() => { haptic.impact('light'); saveFile(book.save_url ?? '', `${book.title || 'audiobook'}.m4b`) }}>{t('books.save')}</Button>
            <Button kind="plain" size="s" icon="trash" loading={busy === 'delete'} onClick={del}>{t('books.delete')}</Button>
          </div>
        </div>
      )}
    </article>
  )
}

export default function Books() {
  const { data, error, refresh } = useBooks()
  const [sample, playSample] = useSample()
  const [up, setUp] = useState<number | null>(null)            // upload progress 0..1 while sending
  const [upErr, setUpErr] = useState('')
  const file = useRef<HTMLInputElement>(null)
  useEffect(() => { try { localStorage.setItem('mh_books_seen', '1') } catch { /* private mode */ } }, [])

  const pick = useCallback(async (f: File | undefined) => {
    if (!f) return
    setUpErr('')
    const mb = data?.limits?.max_mb ?? 100
    if (!/\.(epub|pdf)$/i.test(f.name)) { setUpErr(t('books.bad_type')); return }
    if (f.size > mb * 1024 * 1024) { setUpErr(t('books.too_big', { mb })); return }
    if (inPreview()) { toast(t('books.preview_off')); return }
    haptic.impact('light'); setUp(0)
    try {
      await uploadBook(f, setUp)
      haptic.notify('success'); refresh()
    } catch (e) { haptic.notify('error'); setUpErr((e as Error).message) } finally { setUp(null); if (file.current) file.current.value = '' }
  }, [data, refresh])

  if (!data && error) return <div className="page"><Title>{t('books.title')}</Title><ErrorBox error={error} onRetry={refresh} what={t('err.load')} /></div>
  if (!data) return <div className="page"><Title>{t('books.title')}</Title><Skeleton rows={2} /></div>

  const { books, quota, voices, queue } = data
  const left = quota.unlimited ? Infinity : quota.left
  const canUpload = left > 0 && up == null
  return (
    <div className="page">
      <Title sub={t('books.sub')}>{t('books.title')} <Pill tone="accent">{t('books.beta')}</Pill></Title>

      <section className="bk-beta" aria-labelledby="bk-beta-h">
        <h3 id="bk-beta-h"><Icon name="info" size={18} /> {t('books.beta_h')}</h3>
        <p>{t('books.beta_text', { n: quota.per_user })}</p>
        <Quota quota={quota} />
      </section>

      <section className="sec">
        <div className="group bk-upload">
          <input ref={file} type="file" accept=".epub,.pdf,application/epub+zip,application/pdf" hidden
            onChange={(e) => pick(e.target.files?.[0])} />
          {up != null ? (
            <div className="bk-upload__prog" role="status">
              <p>{t('books.uploading', { p: Math.round(up * 100) })}</p>
              <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(up * 100)}>
                <i style={{ width: `${Math.max(2, up * 100)}%` }} />
              </div>
            </div>
          ) : (
            <Button kind="tonal" icon="plus" block disabled={!canUpload} onClick={() => file.current?.click()}>{t('books.upload')}</Button>
          )}
          {upErr && <p className="formerr" role="alert">{upErr}</p>}
        </div>
        <p className="sec__f">{left > 0 ? t('books.upload_hint', { mb: data.limits?.max_mb ?? 100 }) : t('books.limit_reached')}</p>
      </section>

      <section className="sec">
        <div className="sec__h"><h3>{t('books.mine')}</h3></div>
        {books.length === 0
          ? <div className="group"><Empty icon="book" title={t('books.empty')} text={t('books.empty_text')} /></div>
          : <div className="group">
              {books.map((b) => (
                <BookCard key={b.id} book={b} voices={voices[b.lang] || []} queue={queue} sample={sample} onSample={playSample}
                  onChanged={refresh} limitLeft={left} />
              ))}
            </div>}
        <p className="sec__f">{t('books.foot')}</p>
      </section>
    </div>
  )
}
