import { useMemo, useRef, useState } from 'react'
import { useWidth } from '../hooks.js'
import { Segmented } from '../ui.jsx'
import { t } from '../i18n.js'
import { useApp } from '../ctx.js'
import { fmtDec, fmtShortDay } from '../util.js'

const METRICS = ['users', 'messages', 'app'] // labels: chart.<id>
const H = 190, PAD_T = 10, PAD_B = 24, PAD_L = 30, PAD_R = 4

function niceMax(v) {
  if (v <= 4) return 4
  let e = 1
  for (;;) {
    for (const s of [6, 8, 10, 12, 16, 20, 30, 40, 50, 60, 80, 100]) if (s * e / 10 >= v) return s * e / 10
    e *= 10
  }
}
const bar = (x, y, w, h) => {
  const r = Math.min(4, w / 2, h)
  return `M${x} ${y + h}V${y + r}Q${x} ${y} ${x + r} ${y}H${x + w - r}Q${x + w} ${y} ${x + w} ${y + r}V${y + h}Z`
}

// 30-day bars, one series, zero baseline. Hover/tap a column for the numbers.
export default function Chart({ data }) {
  const { lang } = useApp()
  const [metric, setMetric] = useState('users')
  const [pick, setPick] = useState(null)
  const wrap = useRef(null)
  const width = useWidth(wrap)
  const n = data.length
  const vals = data.map((d) => d[metric])
  const max = Math.max(0, ...vals)
  const top = niceMax(max)
  const avg = n ? vals.reduce((a, b) => a + b, 0) / n : 0
  const plotW = Math.max(0, width - PAD_L - PAD_R)
  const plotH = H - PAD_T - PAD_B
  const slot = n ? plotW / n : 0
  const bw = Math.min(Math.max(2, slot - 2), 16)
  const y = (v) => PAD_T + plotH - (v / top) * plotH
  const ticks = [0, top / 2, top]
  const mLabel = t('chart.' + metric)
  const cur = pick != null ? data[pick] : null

  const labels = useMemo(() => data.map((d, i) => ((n - 1 - i) % 7 === 0 ? fmtShortDay(d.day) : null)), [data, n, lang])

  return (
    <div className="chart">
      <div className="chart__head">
        <div>
          <b className="chart__big">{vals[n - 1] ?? 0}</b>
          <span className="chart__cap">{t('chart.today_avg', { avg: fmtDec(avg) })}</span>
        </div>
        <Segmented items={METRICS.map((id) => ({ id, label: t('chart.' + id) }))} value={metric} onChange={setMetric} />
      </div>
      <div className="chart__plot" ref={wrap} onMouseLeave={() => setPick(null)}>
        {width > 0 && (
          <svg width={width} height={H} role="img" aria-label={t('chart.aria', { n, metric: mLabel })}>
            {ticks.map((tk) => (
              <g key={tk}>
                <line x1={PAD_L} x2={width - PAD_R} y1={y(tk)} y2={y(tk)} className={tk === 0 ? 'ax ax--base' : 'ax'} />
                <text x={PAD_L - 6} y={y(tk) + 4} textAnchor="end" className="axt">{Number.isInteger(tk) ? tk : tk.toFixed(1)}</text>
              </g>
            ))}
            {data.map((d, i) => {
              const v = d[metric]
              const cx = PAD_L + slot * i + slot / 2
              const h = v > 0 ? Math.max(2, (v / top) * plotH) : 0
              return (
                <g key={d.day} className={pick != null && pick !== i ? 'dim' : ''}>
                  {h > 0 && <path d={bar(cx - bw / 2, PAD_T + plotH - h, bw, h)} className="barp"><title>{`${fmtShortDay(d.day)}: ${t('chart.u', { n: d.users })}, ${t('chart.m', { n: d.messages })}, ${t('chart.a', { n: d.app })}`}</title></path>}
                  {labels[i] && <text x={cx} y={H - 6} textAnchor="middle" className="axt">{labels[i]}</text>}
                  <rect x={PAD_L + slot * i} y={PAD_T} width={slot} height={plotH + PAD_B - 6} fill="transparent"
                    onMouseEnter={() => setPick(i)} onClick={() => setPick(pick === i ? null : i)} />
                </g>
              )
            })}
          </svg>
        )}
        {cur && (
          <div className="tip" style={{ left: Math.min(Math.max(PAD_L + slot * pick + slot / 2, 80), width - 80) }} role="status">
            <b>{fmtShortDay(cur.day)}</b>
            <span>{t('chart.u', { n: cur.users })}</span><span>{t('chart.m', { n: cur.messages })}</span><span>{t('chart.a', { n: cur.app })}</span>
          </div>
        )}
      </div>
      <table className="sr-only">
        <caption>{t('chart.caption', { n })}</caption>
        <thead><tr><th>{t('chart.day')}</th><th>{t('chart.users')}</th><th>{t('chart.messages')}</th><th>{t('chart.app')}</th></tr></thead>
        <tbody>{data.map((d) => <tr key={d.day}><td>{d.day}</td><td>{d.users}</td><td>{d.messages}</td><td>{d.app}</td></tr>)}</tbody>
      </table>
    </div>
  )
}
