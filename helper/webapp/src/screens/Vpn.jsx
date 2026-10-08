import { useEffect, useMemo, useRef, useState } from 'react'
import qrcode from 'qrcode-generator'
import Icon from '../icons.jsx'
import { Cell, Dot, ErrorBox, Section, Skeleton, doCopy, Button, Empty } from '../ui.jsx'
import { api } from '../api.js'
import { t } from '../i18n.js'
import { haptic, openLink, openTelegramLink, saveFile } from '../tg.js'
import { fmtBytes, shortUrl } from '../util.js'
import { useApp } from '../ctx.js'
import { Title } from './shared.jsx'

// Error correction trades capacity for resilience: short payloads get M, long ones (AWG
// configs run to a few hundred bytes) step down to L to keep the module grid scannable.
function buildQr(text) {
  const levels = text.length > 400 ? ['L'] : ['M', 'L']
  for (const level of levels) {
    try {
      const q = qrcode(0, level)
      q.addData(text)
      q.make()
      return q
    } catch { /* too long for this level */ }
  }
  return null
}

export function Qr({ text, big }) {
  const qr = useMemo(() => {
    const q = buildQr(text)
    if (!q) return null
    const n = q.getModuleCount()
    let d = ''
    for (let y = 0; y < n; y++) {
      for (let x = 0; x < n; x++) {
        if (q.isDark(y, x)) d += `M${x + 4} ${y + 4}h1v1h-1z`
      }
    }
    return { size: n + 8, d }
  }, [text])
  if (!qr) return <p className="formerr" role="alert">{t('vpn.qr_toobig')}</p>
  return (
    <svg className={'qr' + (big ? ' qr--big' : '')} viewBox={`0 0 ${qr.size} ${qr.size}`} role="img" aria-label="QR" shapeRendering="crispEdges">
      <rect width={qr.size} height={qr.size} fill="#fff" />
      <path d={qr.d} fill="#000" />
    </svg>
  )
}

// GET /api/vpn; a 409 'pending' means the client isn't created yet: retry once.
function useVpn() {
  const [st, setSt] = useState({ loading: true })
  const tries = useRef(0)
  async function load() {
    setSt((s) => ({ ...s, loading: true, error: null }))
    tries.current = 0
    for (;;) {
      try {
        const data = await api('vpn')
        setSt({ data, loading: false }); return
      } catch (error) {
        if (error.code === 'pending' && tries.current++ < 1) {
          setSt({ loading: true, pending: true })
          await new Promise((r) => setTimeout(r, 2000))
          continue
        }
        setSt({ loading: false, error }); return
      }
    }
  }
  useEffect(() => { load() }, []) // eslint-disable-line react-hooks/exhaustive-deps
  return { ...st, reload: load }
}

function LinkCell({ l, i }) {
  const [qr, setQr] = useState(false)
  if (l.action === 'qr') {
    return (
      <>
        <Cell icon="qr" title={qr ? t('vpn.hide_qr') : t('vpn.show_qr')} sub={l.name} onClick={() => { haptic.impact('light'); setQr(!qr) }}
          right={<Icon name="chevdown" size={18} className={'cell__chev rot' + (qr ? ' open' : '')} />} />
        {qr && (
          <div className="qrbox qrbox--big">
            <Qr text={l.text || ''} big />
            <p>{t('vpn.qr_conf_hint')}</p>
          </div>
        )}
      </>
    )
  }
  if (l.action === 'download') {
    const dl = () => { haptic.impact('light'); saveFile(l.url, l.file_name) }
    return (
      <Cell icon="download" title={l.name || l.file_name || `#${i + 1}`} sub={l.file_name} onClick={dl}
        right={<Button kind="tonal" size="s" onClick={(e) => { e.stopPropagation(); dl() }}>{t('vpn.download')}</Button>} />
    )
  }
  if (l.action === 'open') {
    const go = () => { haptic.impact('light'); openLink(new URL(l.url, location.href).href) }
    return (
      <Cell icon="external" title={l.name || `#${i + 1}`} onClick={go}
        right={<Button kind="tonal" size="s" onClick={(e) => { e.stopPropagation(); go() }}>{t('vpn.open')}</Button>} />
    )
  }
  if (l.action === 'telegram') {
    const go = () => { haptic.impact('light'); openTelegramLink(l.url) }
    return (
      <Cell icon="send" title={l.name || `#${i + 1}`} onClick={go}
        right={<Button kind="tonal" size="s" onClick={(e) => { e.stopPropagation(); go() }}>{t('vpn.connect')}</Button>} />
    )
  }
  const copy = () => { haptic.impact('light'); doCopy(l.url) }
  return (
    <Cell icon="key" title={l.name || `#${i + 1}`} onClick={copy}
      right={<button type="button" className="iconbtn" aria-label={t('common.copy')} onClick={(e) => { e.stopPropagation(); copy() }}><Icon name="copy" size={20} /></button>} />
  )
}

// One config family = one set of apps that can use it.
function Group({ g, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section className="sec vgrp">
      <button type="button" className="sec__h sec__h--btn" aria-expanded={open} onClick={() => setOpen(!open)}>
        <h3>{g.title}</h3>
        <Icon name="chevdown" size={16} className={'rot' + (open ? ' open' : '')} />
      </button>
      {g.apps?.length > 0 && <div className="appchips">{g.apps.map((a) => <span key={a} className="appchip">{a}</span>)}</div>}
      {open && (
        <>
          <div className="group">{g.links.map((l, i) => <LinkCell key={i} l={l} i={i} />)}</div>
          {g.hint && <p className="sec__f">{g.hint}</p>}
        </>
      )}
    </section>
  )
}

export default function Vpn() {
  const { go } = useApp()
  const { data, error, loading, pending, reload } = useVpn()
  const [qr, setQr] = useState(false)

  let body
  if (loading && !data) {
    body = (
      <>
        <p className="note note--c">{t('vpn.pending')}</p>
        <Skeleton rows={2} /><Skeleton rows={3} />
      </>
    )
  } else if (error) {
    if (error.code === 'no_access') {
      body = (
        <Empty icon="lock" title={t('vpn.no_access')}
          action={<Button kind="primary" icon="key" onClick={() => go('code', null, true)}>{t('home.enter_code')}</Button>} />
      )
    } else if (error.code === 'vpn_unconfigured') {
      body = <Empty icon="server" title={t('vpn.unconfigured')} />
    } else {
      body = <ErrorBox error={error} onRetry={reload} what={t('err.load')} />
    }
  } else {
    const { sub_url: sub, links = [], traffic, online, apps = [] } = data
    // An older server has no groups: show everything as one.
    const groups = (data.groups?.length ? data.groups : links.length ? [{ id: 'all', title: t('vpn.configs'), apps: [], hint: '', links }] : [])
    const hasSub = !!sub
    body = (
      <>
        {hasSub && (
          <>
            <Section title={t('vpn.sub_all')}>
              <Cell icon="link" title={t('vpn.sub_link')} sub={<span className="mono">{shortUrl(sub)}</span>}
                right={<button type="button" className="iconbtn" aria-label={t('common.copy')} onClick={(e) => { e.stopPropagation(); haptic.impact('light'); doCopy(sub) }}><Icon name="copy" size={20} /></button>} />
              <Cell icon="qr" title={qr ? t('vpn.hide_qr') : t('vpn.show_qr')} onClick={() => { haptic.impact('light'); setQr(!qr) }}
                right={<Icon name="chevdown" size={18} className={'cell__chev rot' + (qr ? ' open' : '')} />} />
              {qr && (
                <div className="qrbox">
                  <Qr text={sub} />
                  <p>{t('vpn.qr_hint')}</p>
                </div>
              )}
            </Section>

            <Section title={t('vpn.add')} footer={t('vpn.add_foot')}>
              {apps.map((a) => (
                <Cell key={a.id} icon="external" title={a.name} sub={Array.isArray(a.platforms) ? a.platforms.join(' · ') : (a.platforms || '')} chevron
                  onClick={() => { haptic.impact('light'); openLink(new URL(a.go_url, location.href).href) }} />
              ))}
            </Section>
          </>
        )}

        <Section title={t('vpn.traffic')}
          action={<span className="online"><Dot on={online} />{online ? t('vpn.online') : t('vpn.offline')}</span>}>
          <Cell icon="down" tone="ok" title={t('vpn.down')} value={fmtBytes(traffic?.down)} />
          <Cell icon="up" title={t('vpn.up')} value={fmtBytes(traffic?.up)} />
        </Section>

        {groups.length === 0 && !hasSub && <Section><Cell icon="info" tone="mute" title={t('vpn.cfg_empty')} /></Section>}
        {groups.map((g) => <Group key={g.id} g={g} defaultOpen={!hasSub} />)}
        {hasSub && <p className="foot">{t('vpn.foot')}</p>}
      </>
    )
  }

  return (
    <div className="page">
      <Title>{t('vpn.title')}</Title>
      {body}
    </div>
  )
}
