import { useEffect, useRef, useState, type CSSProperties } from 'react'
import { motion } from 'framer-motion'

const POLL_MS = 2000

// GET ./api/stats, served by stats/server.py
interface Usage { percent: number; used: number; total: number }
interface HostStats {
  cpu: { percent: number; temp: number | null }
  mem: Usage
  disk: Usage
  hdd: Usage | null
  gpu: { percent: number; memUsed: number; memTotal: number; memPercent?: number; temp: number } | null
  net: { rxBps: number; txBps: number }
  ts: number
}

function fmtBytes(bytes: number): string {
  let n = bytes
  if (n < 1024) return `${n} B`
  const u = ['KB', 'MB', 'GB', 'TB']
  let i = -1
  do {
    n /= 1024
    i++
  } while (n >= 1024 && i < u.length - 1)
  return `${n.toFixed(n < 10 ? 1 : 0)} ${u[i]}`
}

interface GaugeProps { label: string; percent: number | null | undefined; detail: string; from: string; to: string }

function Gauge({ label, percent, detail, from, to }: GaugeProps) {
  const p = Math.max(0, Math.min(100, percent ?? 0))
  return (
    <div className="stat">
      <div className="stat__head">
        <span className="stat__label">{label}</span>
        <span className="stat__pct">{p.toFixed(0)}%</span>
      </div>
      <div className="stat__track">
        <div
          className="stat__fill"
          style={{ '--from': from, '--to': to, transform: `translateX(${p - 100}%)` } as CSSProperties}
        />
      </div>
      <div className="stat__detail">{detail}</div>
    </div>
  )
}

export default function Stats() {
  const [data, setData] = useState<HostStats | null>(null)
  const [err, setErr] = useState(false)
  const timer = useRef(0)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const r = await fetch('./api/stats', { cache: 'no-store' })
        if (!r.ok) throw new Error()
        const j = (await r.json()) as HostStats
        if (alive) {
          setData(j)
          setErr(false)
        }
      } catch {
        if (alive) setErr(true)
      }
    }
    void tick()
    timer.current = window.setInterval(tick, POLL_MS)
    return () => {
      alive = false
      clearInterval(timer.current)
    }
  }, [])

  if (err && !data) {
    return (
      <section className="stats stats--err">system stats unavailable</section>
    )
  }
  if (!data) {
    return <section className="stats stats--load">loading system stats…</section>
  }

  const { cpu, mem, disk, hdd, gpu, net } = data
  return (
    <motion.section
      className="stats"
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
    >
      <Gauge
        label="CPU"
        percent={cpu.percent}
        detail={
          cpu.temp != null
            ? `${cpu.percent.toFixed(1)}% · ${cpu.temp.toFixed(0)}°C`
            : `${cpu.percent.toFixed(1)}% load`
        }
        from="#7928ca"
        to="#ff0080"
      />
      <Gauge
        label="RAM"
        percent={mem.percent}
        detail={`${fmtBytes(mem.used)} / ${fmtBytes(mem.total)}`}
        from="#2563eb"
        to="#00c6fb"
      />
      {/* VRAM and GPU load are separate gauges on purpose: they move
          independently. A loaded model can pin 10GB of memory while the card
          sits at 0% util, so folding memory into the GPU tile's subtitle hid
          whichever of the two actually mattered at the time. */}
      {gpu ? (
        <>
          <Gauge
            label="VRAM"
            percent={gpu.memPercent ?? (gpu.memTotal ? (gpu.memUsed / gpu.memTotal) * 100 : 0)}
            detail={`${fmtBytes(gpu.memUsed)} / ${fmtBytes(gpu.memTotal)}`}
            from="#0d9488"
            to="#5eead4"
          />
          <Gauge
            label="GPU"
            percent={gpu.percent}
            detail={`${gpu.percent.toFixed(0)}% · ${gpu.temp.toFixed(0)}°C`}
            from="#10b981"
            to="#a7f3d0"
          />
        </>
      ) : (
        <>
          <Gauge label="VRAM" percent={0} detail="n/a" from="#334155" to="#64748b" />
          <Gauge label="GPU" percent={0} detail="n/a" from="#334155" to="#64748b" />
        </>
      )}
      <Gauge
        label="SSD"
        percent={disk.percent}
        detail={`${fmtBytes(disk.used)} / ${fmtBytes(disk.total)}`}
        from="#ff8a00"
        to="#ffd074"
      />
      {hdd && (
        <Gauge
          label="HDD"
          percent={hdd.percent}
          detail={`${fmtBytes(hdd.used)} / ${fmtBytes(hdd.total)}`}
          from="#e5a00d"
          to="#f8d074"
        />
      )}
      <div className="stat stat--net">
        <div className="stat__head">
          <span className="stat__label">Network</span>
        </div>
        <div className="net">
          <span className="net__dn">↓ {fmtBytes(net.rxBps)}/s</span>
          <span className="net__up">↑ {fmtBytes(net.txBps)}/s</span>
        </div>
      </div>
    </motion.section>
  )
}
