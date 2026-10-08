import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import Icon from '../icons.jsx'
import { Action, Button, Cell, Chips, Empty, ErrorBox, Field, Pill, Segmented, Skeleton, Switch, toast } from '../ui.jsx'
import { api } from '../api.js'
import { useApi } from '../hooks.js'
import { t } from '../i18n.js'
import { canReadClipboard, haptic, readClipboard, saveFile } from '../tg.js'
import { canDownload, fmtBytes, fmtDur, nowSec, parseClock, plural } from '../util.js'
import { useApp } from '../ctx.js'
import { Title } from './shared.jsx'

const defaultSites = () => ['YouTube', 'RuTube', t('dl.site_vk')]
const isActive = (j) => j.status === 'queued' || j.status === 'running'
const ST_TONE = { queued: 'mute', running: 'accent', done: 'ok', error: 'bad', canceled: 'mute', expired: 'mute' }
const listOf = (r) => (Array.isArray(r) ? r : r?.jobs || r?.items || [])

// GET /api/dl, polled every 2 s while something is queued/running, else every 15 s.
// Paused while the page is hidden; resumes (with an immediate fetch) when it is shown again.
function useJobs() {
  const [st, setSt] = useState({ jobs: null, error: null })
  const kick = useRef(() => {})
  useEffect(() => {
    let stop = false, timer = null, busy = false, again = false
    async function tick() {
      clearTimeout(timer); timer = null
      if (stop || document.hidden) return
      if (busy) { again = true; return }
      busy = true
      let active = false
      try {
        const jobs = listOf(await api('dl'))
        active = jobs.some(isActive)
        if (!stop) setSt({ jobs, error: null })
      } catch (error) {
        if (!stop) setSt((s) => ({ jobs: s.jobs, error }))
      }
      busy = false
      if (stop || document.hidden) return
      if (again) { again = false; tick(); return }
      timer = setTimeout(tick, active ? 2000 : 15000)
    }
    const vis = () => { if (!document.hidden) tick() }
    kick.current = tick
    document.addEventListener('visibilitychange', vis)
    tick()
    return () => { stop = true; clearTimeout(timer); document.removeEventListener('visibilitychange', vis) }
  }, [])
  const mutate = useCallback((fn) => setSt((s) => ({ ...s, jobs: s.jobs ? fn(s.jobs) : s.jobs })), [])
  return { ...st, refresh: () => kick.current(), mutate }
}

function Clip({ value, onChange, bad }) {
  const input = (k, label, ph) => (
    <label className="clip__f">
      <span>{label}</span>
      <input className={'input' + (bad ? ' bad' : '')} inputMode="numeric" autoComplete="off" placeholder={ph}
        value={value[k]} aria-label={`${t('dl.clip')}: ${label}`}
        onChange={(e) => onChange({ ...value, [k]: e.target.value.replace(/[^\d:]/g, '').slice(0, 8) })} />
    </label>
  )
  return <div className="clip">{input('start', t('dl.clip_from'), '0:00')}<span className="clip__dash" aria-hidden="true">–</span>{input('end', t('dl.clip_to'), '1:30')}</div>
}

function Advanced({ extras, audio, sub, setSub, clip, setClip, clipBad, playlist, setPlaylist }) {
  const [open, setOpen] = useState(false)
  const langs = extras.subs || []
  const showSubs = langs.length > 0 && !audio
  const showClip = extras.clip !== false
  const max = extras.playlist_max
  return (
    <section className="sec">
      <div className="group">
        <Cell icon="sliders" tone="mute" title={t('dl.more')} onClick={() => { haptic.impact('light'); setOpen(!open) }}
          right={<Icon name="chevdown" size={18} className={'cell__chev rot' + (open ? ' open' : '')} />} />
        {open && (
          <>
            {showSubs && (
              <div className="adv">
                <p className="form__l">{t('dl.subs')}</p>
                <Segmented value={sub || 'none'} onChange={(v) => setSub(v === 'none' ? '' : v)}
                  items={[{ id: 'none', label: t('dl.subs_none') }, ...langs.map((l) => ({ id: l, label: l }))]} />
              </div>
            )}
            {showClip && (
              <div className="adv">
                <p className="form__l">{t('dl.clip')}</p>
                <Clip value={clip} onChange={setClip} bad={clipBad} />
                <p className="field__h">{t('dl.clip_hint')}</p>
              </div>
            )}
            {max > 0 && (
              <Cell title={t('dl.playlist', { n: max })} sub={t('dl.playlist_sub', { n: max })}
                right={<Switch checked={playlist} label={t('dl.playlist', { n: max })} onChange={setPlaylist} />}
                onClick={() => setPlaylist(!playlist)} />
            )}
          </>
        )}
      </div>
    </section>
  )
}

function fileBtn(f, label, onSave, kind = 'tonal') {
  return <Button key={f.name} kind={kind} size="s" icon="download" onClick={() => onSave(f)}>{label}</Button>
}

function JobRow({ job, audioIds, onRefresh, onGone }) {
  const [busy, setBusy] = useState('')
  const active = isActive(job)
  const files = job.files || []
  const main = files.filter((f) => f.kind !== 'subs')
  const subs = files.filter((f) => f.kind === 'subs')
  const audio = main[0]?.kind === 'audio' || (audioIds ? audioIds.has(job.preset) : !/^v(\d+|best)$/.test(job.preset || ''))
  const left = job.expires_ts ? job.expires_ts - nowSec() : null

  async function run(name, fn) {
    if (busy) return
    haptic.impact('light'); setBusy(name)
    try { await fn() } catch (e) { haptic.notify('error'); toast(e.message, 'bad') } finally { setBusy('') }
  }
  const save = (f) => { haptic.impact('light'); saveFile(f.url, f.name) }
  const cancel = () => run('cancel', async () => { await api(`dl/${job.id}/cancel`, { method: 'POST' }); onRefresh() })
  const del = () => run('delete', async () => { await api(`dl/${job.id}/delete`, { method: 'POST' }); onGone(job.id) })

  const eta = typeof job.eta === 'number' ? fmtDur(job.eta) : job.eta
  return (
    <div className={'job' + (job.status === 'error' ? ' job--bad' : '')}>
      <div className="job__top">
        <span className={'tile tile--' + (job.status === 'error' ? 'bad' : active ? 'accent' : 'mute')}><Icon name={audio ? 'music' : 'film'} size={18} /></span>
        <div className="job__body">
          <div className="job__title">{job.title || job.url}</div>
          <div className="job__meta">
            {job.preset_label && <span className="appchip">{job.preset_label}</span>}
            {job.size != null && job.status !== 'queued' && <span>{fmtBytes(job.size)}</span>}
            {job.status === 'done' && left != null && left > 0 && <span>{t('dl.expires', { t: fmtDur(left) })}</span>}
          </div>
        </div>
        <Pill tone={ST_TONE[job.status] || 'mute'}>{t('dl.st.' + job.status)}</Pill>
      </div>

      {active && (
        <div className="job__prog">
          <div className={'bar' + (job.status === 'queued' || job.percent == null ? ' bar--idle' : '')} role="progressbar"
            aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.percent ?? undefined}>
            <i style={{ width: job.status === 'queued' || job.percent == null ? undefined : `${Math.min(100, Math.max(2, job.percent))}%` }} />
          </div>
          <div className="job__pl">
            <span>{job.status === 'queued' ? t('dl.queued_hint') : job.percent != null ? `${Math.round(job.percent)}%` : ''}</span>
            {job.status === 'running' && eta ? <span>{t('dl.eta', { t: eta })}</span> : null}
          </div>
        </div>
      )}
      {job.status === 'error' && job.error && <p className="job__err">{job.error}</p>}

      {job.status === 'done' && main.length > 1 && (
        <div className="job__files">
          <p className="form__l">{t('dl.files_n', { n: main.length })}</p>
          {main.map((f) => (
            <div key={f.name} className="job__file">
              <span className="job__fn">{f.name}</span>
              <span className="job__fs">{fmtBytes(f.size)}</span>
              <button type="button" className="iconbtn iconbtn--in" aria-label={`${t('dl.save')}: ${f.name}`} onClick={() => save(f)}><Icon name="download" size={18} /></button>
            </div>
          ))}
        </div>
      )}

      <div className="job__act">
        {job.status === 'done' && main.length === 1 && fileBtn(main[0], t('dl.save'), save, 'primary')}
        {job.status === 'done' && subs.map((f) => fileBtn(f, `${t('dl.subs_file')}${subs.length > 1 ? ' ' + (f.lang || f.name.split('.').slice(-2, -1)[0] || '') : ''}`, save))}
        {active && <Button kind="danger" size="s" icon="x" loading={busy === 'cancel'} onClick={cancel}>{t('dl.cancel')}</Button>}
        {!active && <Button kind="plain" size="s" icon="trash" loading={busy === 'delete'} onClick={del}>{t('dl.delete')}</Button>}
      </div>
    </div>
  )
}

export default function Downloads() {
  const { me } = useApp()
  const allowed = canDownload(me)
  const { data: presets, error: presetsErr, loading: presetsLoading, reload: reloadPresets } = useApi(allowed ? 'dl/presets' : null)
  const { jobs, error: jobsErr, refresh, mutate } = useJobs()

  const [url, setUrl] = useState(() => new URLSearchParams(location.search).get('url') || '')
  const [preset, setPreset] = useState('')
  const [sub, setSub] = useState('')
  const [clip, setClip] = useState({ start: '', end: '' })
  const [playlist, setPlaylist] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const canPaste = useMemo(canReadClipboard, [])

  // Default to 720p (or the first video preset) once presets are known.
  useEffect(() => {
    if (!presets || preset) return
    const v = presets.video || []
    setPreset((v.find((p) => p.id === 'v720') || v[0] || (presets.audio || [])[0])?.id || '')
  }, [presets, preset])

  const audioIds = useMemo(() => (presets ? new Set((presets.audio || []).map((p) => p.id)) : null), [presets])
  const isAudio = !!audioIds?.has(preset)
  const extras = presets?.extras || {}
  const sites = presets?.sites?.length ? presets.sites : defaultSites()
  const ttl = presets?.limits?.ttl_min ?? 30

  const cs = parseClock(clip.start), ce = parseClock(clip.end)
  const clipUsed = clip.start.trim() !== '' || clip.end.trim() !== ''
  let clipErr = ''
  if (clipUsed) {
    if (Number.isNaN(cs) || Number.isNaN(ce) || cs == null || ce == null) clipErr = t('dl.clip_both')
    else if (ce <= cs) clipErr = t('dl.clip_bad')
  }
  const trimmed = url.trim()
  const ready = !!trimmed && !!preset && !clipErr

  async function paste() {
    haptic.impact('light')
    const txt = await readClipboard()
    if (!txt) { toast(t('dl.paste_fail'), 'bad'); return }
    setUrl(txt.trim()); setErr('')
  }

  async function submit() {
    if (busy) return
    if (!ready) { if (clipErr) setErr(clipErr); return }
    if (!/^https?:\/\/\S+$/i.test(trimmed)) { haptic.notify('error'); setErr(t('dl.url_bad')); return }
    haptic.impact('light')
    setBusy(true); setErr('')
    const body = { url: trimmed, preset }
    if (sub && !isAudio) body.subs = sub
    if (clipUsed) body.clip = { start: cs, end: ce }
    if (playlist) body.playlist = true
    try {
      const job = await api('dl', { method: 'POST', body })
      haptic.notify('success'); toast(t('dl.queued_toast'))
      setUrl(''); setClip({ start: '', end: '' }); setPlaylist(false)
      if (job?.id != null) mutate((js) => [job, ...js.filter((j) => j.id !== job.id)])
      refresh()
    } catch (e) { haptic.notify('error'); setErr(e.message) } finally { setBusy(false) }
  }

  if (!allowed) {
    return (
      <div className="page">
        <Title>{t('dl.title')}</Title>
        <Empty icon="lock" title={t('dl.off')} />
      </div>
    )
  }

  const chipRow = (list, label) => list?.length > 0 && (
    <div className="presets">
      <p className="form__l">{label}</p>
      <Chips items={list.map((p) => ({ id: p.id, label: p.label }))} value={preset} onChange={(id) => { setPreset(id); setErr('') }} />
    </div>
  )

  return (
    <div className="page">
      <Title>{t('dl.title')}</Title>

      <Field label={t('dl.url')} error={err}
        suffix={trimmed
          ? <button type="button" className="iconbtn iconbtn--in" aria-label={t('dl.clear')} onClick={() => { setUrl(''); setErr('') }}><Icon name="x" size={18} /></button>
          : canPaste ? <Button kind="tonal" size="s" icon="clipboard" onClick={paste}>{t('dl.paste')}</Button> : null}>
        <input className="input" type="url" inputMode="url" autoCapitalize="none" autoCorrect="off" spellCheck={false}
          placeholder="https://…" value={url} enterKeyHint="go"
          onChange={(e) => { setUrl(e.target.value); setErr('') }}
          onKeyDown={(e) => { if (e.key === 'Enter') submit() }} />
      </Field>

      {presetsLoading && !presets && <Skeleton rows={2} title={false} />}
      {presetsErr && !presets && <ErrorBox error={presetsErr} onRetry={reloadPresets} what={t('err.load')} />}
      {presets && (
        <>
          {chipRow(presets.video, t('dl.video'))}
          {chipRow(presets.audio, t('dl.audio'))}
          <Advanced extras={extras} audio={isAudio} sub={sub} setSub={setSub} clip={clip} setClip={setClip}
            clipBad={!!clipErr} playlist={playlist} setPlaylist={setPlaylist} />
          {clipErr && <p className="formerr" role="alert">{clipErr}</p>}
        </>
      )}

      <Action text={t('dl.go')} icon="download" enabled={ready} loading={busy} onClick={submit} />

      <section className="sec jobs">
        <div className="sec__h"><h3>{t('dl.mine')}</h3></div>
        {jobs == null && !jobsErr && <Skeleton rows={2} title={false} />}
        {jobs == null && jobsErr && <ErrorBox error={jobsErr} onRetry={refresh} what={t('err.load')} />}
        {jobs && jobs.length === 0 && (
          <div className="group"><Empty icon="film" title={t('dl.empty', { sites: sites.join(', ') })} /></div>
        )}
        {jobs && jobs.length > 0 && (
          <div className="group">
            {jobs.map((j) => (
              <JobRow key={j.id} job={j} audioIds={audioIds} onRefresh={refresh}
                onGone={(id) => { mutate((js) => js.filter((x) => x.id !== id)); refresh() }} />
            ))}
          </div>
        )}
        <p className="sec__f">{t('dl.foot', { min: ttl, unit: plural(ttl, t('dl.unit_min').split('|')), sites: sites.join(', ') })}</p>
      </section>
    </div>
  )
}
