import { useCallback, useEffect, useRef, useState } from 'react'
import Icon from '../icons.jsx'
import { Button, Empty, ErrorBox, Pill, Segmented, Skeleton, toast, useAuthedImage } from '../ui.jsx'
import { api, inPreview } from '../api.js'
import { getLang, t } from '../i18n.js'
import { confirmDialog, haptic, initData, isTg, openLink, saveFile } from '../tg.js'
import { fmtBytes, fmtDur, plural } from '../util.js'
import { Title } from './shared.jsx'

// Photo upscaling: pick a mode, send pictures, compare before/after, save. Pictures are quick
// (seconds to a minute), so the page polls while anything is in line instead of a bot message.
const LIVE = new Set(['queued', 'running'])
const PILL = { queued: 'mute', running: 'accent', done: 'ok', error: 'bad', cancelled: 'mute' }
const IMAGE_RE = /\.(jpe?g|png|webp|bmp|tiff?|gif)$/i
const picsWord = (n) => plural(n, t('up.unit_pics').split('|'))

function pref(k, fallback) { try { return localStorage.getItem('mh_up_' + k) || fallback } catch { return fallback } }
function setPref(k, v) { try { localStorage.setItem('mh_up_' + k, v) } catch { /* private mode */ } }

// GET /api/upscale every 1.5 s while something is in line, else every 30 s; paused while hidden.
function useJobs() {
  const [st, setSt] = useState({ data: null, error: null })
  const kick = useRef(() => {})
  useEffect(() => {
    let stop = false, timer = null
    async function tick() {
      clearTimeout(timer)
      if (stop || document.hidden) return
      let busy = false
      try {
        const data = await api('upscale')
        busy = data.jobs.some((j) => LIVE.has(j.status))
        if (!stop) setSt({ data, error: null })
      } catch (error) {
        if (!stop) setSt((s) => ({ data: s.data, error }))
      }
      if (!stop) timer = setTimeout(tick, busy ? 1500 : 30000)
    }
    const vis = () => { if (!document.hidden) tick() }
    kick.current = tick
    document.addEventListener('visibilitychange', vis)
    tick()
    return () => { stop = true; clearTimeout(timer); document.removeEventListener('visibilitychange', vis) }
  }, [])
  return { ...st, refresh: () => kick.current() }
}

// Upload with progress (fetch can't report upload progress; XHR can).
function uploadPic(file, opts, onProgress) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest()
    x.open('POST', `./api/upscale/upload?preset=${opts.preset}&scale=${opts.scale}&format=${opts.format}`)
    if (isTg) x.setRequestHeader('X-Tg-Init-Data', initData)
    x.setRequestHeader('X-Lang', getLang())
    x.setRequestHeader('X-Filename', encodeURIComponent(file.name || 'image.jpg'))
    x.setRequestHeader('Content-Type', file.type && file.type.startsWith('image/') ? file.type : 'application/octet-stream')
    x.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total) }
    x.onload = () => {
      let d = null
      try { d = JSON.parse(x.responseText) } catch { /* not JSON */ }
      if (x.status >= 200 && x.status < 300) resolve(d)
      else reject(new Error(d?.message || t('err.generic')))
    }
    x.onerror = () => reject(new Error(t('err.network')))
    x.send(file)
  })
}

// Before/after: the "after" layer is clipped at the handle. Pointer drags move it (touch-action
// pan-y keeps vertical scrolling); a visually hidden range input does the same by keyboard.
function Compare({ job }) {
  const before = useAuthedImage(job.demo ? null : `./api/upscale/${job.id}/before`)
  const after = useAuthedImage(job.demo ? null : `./api/upscale/${job.id}/after`)
  const [at, setAt] = useState(50)
  const box = useRef(null)
  const drag = useRef(false)
  const move = (e) => {
    const r = box.current.getBoundingClientRect()
    setAt(Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100)))
  }
  const bSrc = job.demo ? job.demo.before : before.src
  const aSrc = job.demo ? job.demo.after : after.src
  return (
    <div className="up-cmp" ref={box} style={{ '--at': at + '%', aspectRatio: `${job.in_w} / ${job.in_h}` }}
      onPointerDown={(e) => { drag.current = true; e.currentTarget.setPointerCapture?.(e.pointerId); move(e) }}
      onPointerMove={(e) => { if (drag.current) move(e) }}
      onPointerUp={() => { drag.current = false }} onPointerCancel={() => { drag.current = false }}>
      {bSrc ? <img src={bSrc} alt="" draggable="false" /> : <span className="spin" />}
      {aSrc && <div className="up-cmp__after"><img src={aSrc} alt="" draggable="false" /></div>}
      <div className="up-cmp__line" aria-hidden="true"><span><Icon name="chevleft" size={14} /><Icon name="chevron" size={14} /></span></div>
      <span className="up-cmp__tag up-cmp__tag--l">{t('up.before')}</span>
      <span className="up-cmp__tag up-cmp__tag--r">{t('up.after')}</span>
      <input className="sr-only" type="range" min="0" max="100" value={Math.round(at)} aria-label={t('up.compare')}
        onChange={(e) => setAt(+e.target.value)} />
    </div>
  )
}

function Thumb({ job }) {
  const img = useAuthedImage(job.demo ? null : `./api/upscale/${job.id}/before`)
  const src = job.demo ? job.demo.before : img.src
  return (
    <div className="up-thumb" style={{ aspectRatio: `${job.in_w} / ${job.in_h}` }}>
      {src ? <img src={src} alt="" /> : <Icon name="image" size={24} />}
      {job.status === 'running' && <div className="up-thumb__scan" style={{ transform: `scaleX(${Math.max(0.03, job.progress / 100)})` }} />}
    </div>
  )
}

function JobCard({ job, onChanged }) {
  const [busy, setBusy] = useState('')
  async function run(name, fn) {
    if (busy) return
    haptic.impact('light'); setBusy(name)
    try { await fn() } catch (e) { haptic.notify('error'); toast(e.message, 'bad') } finally { setBusy('') }
  }
  const cancel = () => run('cancel', async () => { await api(`upscale/${job.id}/cancel`, { method: 'POST' }); onChanged() })
  const del = () => run('delete', async () => {
    if (!(await confirmDialog(t('up.delete_q')))) return
    await api(`upscale/${job.id}/delete`, { method: 'POST' }); onChanged()
  })
  const w = job.wait
  const done = job.status === 'done'
  return (
    <article className="up-job" aria-busy={LIVE.has(job.status) || undefined}>
      {done ? <Compare job={job} /> : <Thumb job={job} />}
      <div className="up-job__row">
        <div className="up-job__body">
          <h4 className="up-job__name">{job.name}</h4>
          <p className="up-job__meta">
            {job.in_w}×{job.in_h} → {job.out_w}×{job.out_h}
            {done && job.out_size ? <> · {fmtBytes(job.out_size)}</> : null}
            {' · '}{t('up.preset.' + job.preset)}
          </p>
        </div>
        <Pill tone={PILL[job.status] || 'mute'}>{t('up.st.' + job.status)}</Pill>
      </div>
      {job.scale !== job.scale_asked && <p className="up-job__note">{t('up.scaled_down', { s: job.scale, a: job.scale_asked })}</p>}
      {LIVE.has(job.status) && (
        <p className="up-job__note" role="status">
          {job.status === 'running' ? `${Math.floor(job.progress)}% · ` : w && w.ahead > 0 ? t('up.ahead', { n: w.ahead, pics: picsWord(w.ahead) }) + ' · ' : ''}
          {w ? t('up.ready_in', { t: fmtDur(Math.max(1, w.ready_in)) }) : t('up.queued')}
        </p>
      )}
      {job.status === 'error' && <p className="bk__err">{t('up.failed')}</p>}
      <div className="bk__act">
        {done && <Button kind="primary" size="s" icon="download" onClick={() => { haptic.impact('light'); saveFile(job.save_url, `${job.name}-x${job.scale}.${job.format}`) }}>{t('up.save')}</Button>}
        {done && <Button kind="plain" size="s" icon="external" onClick={() => { haptic.impact('light'); openLink(job.view_url) }}>{t('up.open')}</Button>}
        {LIVE.has(job.status)
          ? <Button kind="plain" size="s" icon="x" loading={busy === 'cancel'} onClick={cancel}>{t('up.cancel')}</Button>
          : <Button kind="plain" size="s" icon="trash" loading={busy === 'delete'} onClick={del}>{t('up.delete')}</Button>}
      </div>
    </article>
  )
}

function Modes({ presets, value, onChange }) {
  return (
    <div className="bk-voices up-modes" role="radiogroup" aria-label={t('up.mode')}>
      {presets.map((p) => (
        <div key={p} className={'bk-voice' + (value === p ? ' on' : '')}>
          <label>
            <input type="radio" name="up-mode" checked={value === p} onChange={() => { haptic.select(); onChange(p) }} />
            <span className="bk-voice__n">{t('up.preset.' + p)}</span>
            <span className="bk-voice__m">{t('up.preset_d.' + p)}</span>
          </label>
        </div>
      ))}
    </div>
  )
}

export default function Upscale() {
  const { data, error, refresh } = useJobs()
  const [preset, setPreset] = useState(() => pref('preset', 'photo'))
  const [scale, setScale] = useState(() => pref('scale', '4'))
  const [format, setFormat] = useState(() => pref('format', 'auto'))
  const [up, setUp] = useState(null)            // { i, n, p } while sending
  const [upErr, setUpErr] = useState('')
  const file = useRef(null)
  useEffect(() => { try { localStorage.setItem('mh_up_seen', '1') } catch { /* private mode */ } }, [])

  const pick = useCallback(async (files) => {
    files = [...(files || [])]
    if (!files.length) return
    setUpErr('')
    if (inPreview()) { toast(t('books.preview_off')); return }
    const mb = data?.limits?.max_mb ?? 25
    haptic.impact('light')
    let sent = 0
    for (let i = 0; i < files.length; i++) {
      const f = files[i]
      if (f.name && !IMAGE_RE.test(f.name) && !(f.type || '').startsWith('image/')) { setUpErr(t('up.bad_type')); continue }
      if (f.size > mb * 1024 * 1024) { setUpErr(t('up.too_big', { mb })); continue }
      setUp({ i: i + 1, n: files.length, p: 0 })
      try {
        await uploadPic(f, { preset, scale, format }, (p) => setUp({ i: i + 1, n: files.length, p }))
        sent++
        refresh()
      } catch (e) { haptic.notify('error'); setUpErr(e.message); break }
    }
    setUp(null)
    if (file.current) file.current.value = ''
    if (sent) { haptic.notify('success'); refresh() }
  }, [data, preset, scale, format, refresh])

  if (!data && error) return <div className="page"><Title>{t('up.title')}</Title><ErrorBox error={error} onRetry={refresh} what={t('err.load')} /></div>
  if (!data) return <div className="page"><Title>{t('up.title')}</Title><Skeleton rows={2} /></div>

  const { jobs, quota, presets, queue } = data
  const left = quota.unlimited ? Infinity : quota.left
  const set = (k, setter) => (v) => { setter(v); setPref(k, v) }
  return (
    <div className="page">
      <Title sub={t('up.sub')}>{t('up.title')}</Title>

      <section className="sec">
        <div className="sec__h"><h3>{t('up.mode')}</h3></div>
        <Modes presets={presets} value={presets.includes(preset) ? preset : presets[0]} onChange={set('preset', setPreset)} />
        <div className="up-opts">
          <div>
            <p className="form__l">{t('up.scale')}</p>
            <Segmented items={['2', '3', '4'].map((s) => ({ id: s, label: s + '×' }))} value={scale} onChange={set('scale', setScale)} />
          </div>
          <div>
            <p className="form__l">{t('up.format')}</p>
            <Segmented items={[{ id: 'auto', label: t('up.format_auto') }, { id: 'jpg', label: 'JPG' }, { id: 'png', label: 'PNG' }]}
              value={format} onChange={set('format', setFormat)} />
          </div>
        </div>
      </section>

      <section className="sec">
        <div className="group bk-upload">
          <input ref={file} type="file" accept="image/*" multiple hidden onChange={(e) => pick(e.target.files)} />
          {up != null ? (
            <div className="bk-upload__prog" role="status">
              <p>{up.n > 1 ? t('up.uploading_n', { i: up.i, n: up.n, p: Math.round(up.p * 100) }) : t('up.uploading', { p: Math.round(up.p * 100) })}</p>
              <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(up.p * 100)}>
                <i style={{ width: `${Math.max(2, up.p * 100)}%` }} />
              </div>
            </div>
          ) : (
            <Button kind="primary" icon="plus" block disabled={left <= 0 || data.online === false} onClick={() => file.current?.click()}>{t('up.pick')}</Button>
          )}
          {upErr && <p className="formerr" role="alert">{upErr}</p>}
        </div>
        <p className="sec__f">
          {quota.unlimited ? t('up.quota_owner')
            : left > 0 ? t('up.quota_left', { n: left, total: quota.per_day })
              : t('up.limit_reached', { t: fmtDur(quota.next_in || 3600) })}
          {queue && queue.jobs > 0 ? ' ' + t('up.line_now', { n: queue.jobs, pics: picsWord(queue.jobs), t: fmtDur(Math.max(1, queue.wait)) }) : ''}
        </p>
      </section>

      <section className="sec">
        <div className="sec__h"><h3>{t('up.mine')}</h3></div>
        {jobs.length === 0
          ? <div className="group"><Empty icon="image" title={t('up.empty')} text={t('up.empty_text')} /></div>
          : <div className="group">{jobs.map((j) => <JobCard key={j.id} job={j} onChanged={refresh} />)}</div>}
        <p className="sec__f">{t('up.foot', { mb: data.limits?.max_mb ?? 25 })}</p>
      </section>
    </div>
  )
}
