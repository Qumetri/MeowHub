import { useEffect, useRef, useState } from 'react'
import Duck from './Duck'

// A mother duck and her ducklings swim around the inside of the window frame,
// feet always towards the nearest edge (so they ride the top edge upside down).
//
// Each lap is precomputed as transform keyframes and handed to the Web
// Animations API, so the compositor moves the ducks on its own and no script
// runs per frame. The waddle is a CSS animation on an inner element, and a
// quack is a one-off hop on another; three nested transforms, all GPU-only.

const SPEED = 38 // px per second along the frame
const MAX_DUCKS = 14
const STEP = 8 // px between keyframes

type Point = [x: number, y: number]
interface Lap { total: number; keyframes: Keyframe[] }
interface Duckling { key: number; mother: boolean }

// Rounded rectangle traced counter-clockwise on screen: bottom edge left→right,
// up the right edge, top edge right→left, down the left edge.
function frame(w: number, h: number, m: number, r: number): Point[] {
  const pts: Point[] = []
  const arc = (cx: number, cy: number, a0: number, a1: number) => {
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
  pts.push(pts[0]!)
  return pts
}

// One lap as keyframes for a duck of size s: its feet ride the path, heading
// unwrapped so rotation never spins the long way round between keyframes.
function lap(pts: Point[], s: number): Lap {
  const frames: { x: number; y: number; a: number }[] = []
  let heading = 0
  let prev: number | null = null
  const push = (x: number, y: number, a: number) => {
    if (prev !== null) {
      let d = a - prev
      while (d > 180) d -= 360
      while (d <= -180) d += 360
      heading += d
    } else heading = a
    prev = a
    frames.push({ x, y, a: heading })
  }
  for (let i = 1; i < pts.length; i++) {
    const [x0, y0] = pts[i - 1]!
    const [x1, y1] = pts[i]!
    const len = Math.hypot(x1 - x0, y1 - y0)
    if (!len) continue
    const a = (Math.atan2(y1 - y0, x1 - x0) * 180) / Math.PI
    const n = Math.max(1, Math.round(len / STEP))
    for (let k = 0; k < n; k++) push(x0 + ((x1 - x0) * k) / n, y0 + ((y1 - y0) * k) / n, a)
  }
  const total = frames.length * STEP
  const first = frames[0]!
  frames.push({ ...first, a: first.a + Math.round((heading - first.a) / 360) * 360 })
  return {
    total,
    keyframes: frames.map((f) => ({
      transform: `translate(${(f.x - s / 2).toFixed(1)}px, ${(f.y - s * 0.81).toFixed(1)}px) rotate(${f.a.toFixed(1)}deg)`,
    })),
  }
}

function makeBrood(n: number): Duckling[] {
  return Array.from({ length: n }, (_, i) => ({ key: i, mother: i === 0 }))
}

export default function Parade() {
  const [brood, setBrood] = useState(() => makeBrood(5))
  const refs = useRef<(HTMLSpanElement | null)[]>([])
  // Shared clock so a new duckling, or a resize, slots into the same parade.
  const origin = useRef(performance.now())

  // The hero duck adds ducklings (see Hero in App.jsx).
  useEffect(() => {
    const add = () => setBrood((b) => (b.length >= MAX_DUCKS ? b : makeBrood(b.length + 1)))
    window.addEventListener('duck:add', add)
    return () => window.removeEventListener('duck:add', add)
  }, [])

  useEffect(() => {
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const box = refs.current[0]?.parentElement
    if (!box) return
    let anims: Animation[] = []

    const layout = () => {
      anims.forEach((a) => a.cancel())
      anims = []
      const w = box.clientWidth
      const h = box.clientHeight
      const size = w < 640 ? 22 : 30
      const pts = frame(w, h, w < 640 ? 3 : 6, 34)
      const laps: Record<number, Lap> = {}
      refs.current.forEach((el, i) => {
        if (!el) return
        const s = i === 0 ? size : Math.round(size * 0.68)
        const { keyframes, total } = (laps[s] ??= lap(pts, s))
        const gap = i === 0 ? 0 : size * 1.25 + (i - 1) * size * 0.92
        const duration = (total / SPEED) * 1000
        const behind = (gap / SPEED) * 1000
        el.style.width = `${s}px`
        if (still) {
          el.style.transform = String(keyframes[Math.floor(((0.12 * total - gap + total) % total) / STEP)]?.transform ?? '')
          return
        }
        const a = el.animate(keyframes, { duration, iterations: Infinity, easing: 'linear' })
        a.currentTime = (((performance.now() - origin.current - behind) % duration) + duration) % duration
        anims.push(a)
      })
    }
    layout()

    // Phones resize on every toolbar show/hide while scrolling; coalesce, and
    // ignore height-only nudges smaller than the toolbar itself.
    let pending = 0
    let lastW = box.clientWidth
    let lastH = box.clientHeight
    const onResize = () => {
      clearTimeout(pending)
      pending = window.setTimeout(() => {
        if (box.clientWidth === lastW && Math.abs(box.clientHeight - lastH) < 4) return
        lastW = box.clientWidth
        lastH = box.clientHeight
        layout()
      }, 200)
    }
    window.addEventListener('resize', onResize)
    return () => {
      clearTimeout(pending)
      window.removeEventListener('resize', onResize)
      anims.forEach((a) => a.cancel())
    }
  }, [brood.length])

  const quack = (i: number) => {
    const el = refs.current[i]
    if (!el) return
    ;(el.firstElementChild as HTMLElement).animate(
      [{ transform: 'translateY(0)' }, { transform: 'translateY(-90%)' }, { transform: 'translateY(0)' }],
      { duration: 450, easing: 'ease-out' },
    )
    el.classList.remove('parade__duck--quack')
    void el.offsetWidth
    el.classList.add('parade__duck--quack')
  }

  return (
    <div className="parade" aria-hidden="true">
      {brood.map((d, i) => (
        <span
          key={d.key}
          ref={(el) => { refs.current[i] = el }}
          className={`parade__duck ${d.mother ? 'parade__duck--mother' : ''}`}
          onPointerDown={() => quack(i)}
        >
          <span className="parade__hop">
            <span className="parade__waddle" style={{ animationDelay: `${i * -0.37}s` }}>
              <Duck ears={d.mother} size={64} />
            </span>
            <span className="parade__quack">quack</span>
          </span>
        </span>
      ))}
    </div>
  )
}
