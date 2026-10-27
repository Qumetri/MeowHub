import { useEffect, useRef, useState } from 'react'
import Duck from './Duck.jsx'

// A mother duck and her ducklings swim around the inside of the window frame,
// feet always towards the nearest edge (so they ride the top edge upside down).
// Positions are written straight into each element's transform from one rAF
// loop; React only renders when the brood grows.

const SPEED = 38 // px per second along the frame
const MAX_DUCKS = 14

// Rounded rectangle traced counter-clockwise on screen: bottom edge left→right,
// up the right edge, top edge right→left, down the left edge. Returns a sampler
// d ↦ {x, y, a} where a is the heading in degrees (0 = moving right).
function frame(w, h, m, r) {
  const pts = []
  const arc = (cx, cy, a0, a1) => {
    for (let i = 1; i <= 16; i++) {
      const a = ((a0 + ((a1 - a0) * i) / 16) * Math.PI) / 180
      pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)])
    }
  }
  pts.push([m + r, h - m])
  pts.push([w - m - r, h - m])
  arc(w - m - r, h - m - r, 90, 0)
  pts.push([w - m, m + r])
  arc(w - m - r, m + r, 0, -90)
  pts.push([m + r, m])
  arc(m + r, m + r, -90, -180)
  pts.push([m, h - m - r])
  arc(m + r, h - m - r, 180, 90)

  const cum = [0]
  for (let i = 1; i < pts.length; i++) {
    cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]))
  }
  const total = cum[cum.length - 1]

  const at = (d) => {
    d = ((d % total) + total) % total
    let lo = 0
    let hi = cum.length - 1
    while (hi - lo > 1) {
      const mid = (lo + hi) >> 1
      if (cum[mid] <= d) lo = mid
      else hi = mid
    }
    const [x0, y0] = pts[lo]
    const [x1, y1] = pts[hi]
    const seg = cum[hi] - cum[lo] || 1
    const t = (d - cum[lo]) / seg
    return {
      x: x0 + (x1 - x0) * t,
      y: y0 + (y1 - y0) * t,
      a: (Math.atan2(y1 - y0, x1 - x0) * 180) / Math.PI,
    }
  }
  return { at, total }
}

function makeBrood(n) {
  return Array.from({ length: n }, (_, i) => ({ key: i, mother: i === 0 }))
}

export default function Parade() {
  const [brood, setBrood] = useState(() => makeBrood(5))
  const refs = useRef([])
  const quacks = useRef(new Map())

  // The hero duck adds ducklings (see Hero in App.jsx).
  useEffect(() => {
    const add = () => setBrood((b) => (b.length >= MAX_DUCKS ? b : makeBrood(b.length + 1)))
    window.addEventListener('duck:add', add)
    return () => window.removeEventListener('duck:add', add)
  }, [])

  useEffect(() => {
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let path = null
    let size = 0
    const layout = () => {
      const w = window.innerWidth
      const h = window.innerHeight
      size = w < 640 ? 22 : 30
      path = frame(w, h, w < 640 ? 3 : 6, 34)
    }
    layout()
    window.addEventListener('resize', layout)

    let raf = 0
    const start = performance.now()
    const tick = (now) => {
      const t = (now - start) / 1000
      const head = still ? path.total * 0.12 : t * SPEED
      refs.current.forEach((el, i) => {
        if (!el) return
        const mother = i === 0
        const s = mother ? size : size * 0.68
        const gap = i === 0 ? 0 : size * 1.25 + (i - 1) * size * 0.92
        const p = path.at(head - gap)
        // Waddle: a small rock around the feet plus a bob away from the edge.
        const rock = still ? 0 : Math.sin(t * 7 + i * 1.7) * 6
        const bob = still ? 0 : Math.abs(Math.sin(t * 3.5 + i)) * -2.5
        const hop = quacks.current.get(i)
        const jump = hop ? Math.max(0, 1 - (now - hop) / 450) : 0
        const lift = bob - Math.sin(jump * Math.PI) * s * 0.9
        el.style.width = `${s}px`
        el.style.transform =
          `translate(${p.x - s / 2}px, ${p.y - s * 0.81}px) rotate(${p.a}deg) ` +
          `translateY(${lift}px) rotate(${rock}deg)`
      })
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('resize', layout)
    }
  }, [])

  const quack = (i) => {
    quacks.current.set(i, performance.now())
    const el = refs.current[i]
    if (!el) return
    el.classList.remove('parade__duck--quack')
    void el.offsetWidth
    el.classList.add('parade__duck--quack')
  }

  return (
    <div className="parade" aria-hidden="true">
      {brood.map((d, i) => (
        <span
          key={d.key}
          ref={(el) => (refs.current[i] = el)}
          className={`parade__duck ${d.mother ? 'parade__duck--mother' : ''}`}
          onPointerDown={() => quack(i)}
        >
          <Duck ears={d.mother} size={64} />
          <span className="parade__quack">quack</span>
        </span>
      ))}
    </div>
  )
}
