import { useEffect, useRef } from 'react'

// The page background is a pond at night: the old gradient blobs are now
// light on the water, a few lily pads drift, and the surface ripples where the
// pointer moves or taps (and, now and then, under a raindrop). The canvas loop
// only runs while there are rings to draw.

const RING = 'rgba(170, 205, 255, '

// A round pad with a narrow notch and veins fanning out from the stem.
function LilyPad({ className }) {
  const veins = [20, 62, 104, 146, 188, 230, 272, 314]
  return (
    <svg className={`pad ${className}`} viewBox="0 0 100 100" aria-hidden="true">
      <path d="M50 50 L93.5 45 A44 44 0 1 1 92 36 Z" fill="#1d6a4c" />
      <path d="M50 50 L93.5 45 A44 44 0 1 1 92 36 Z" fill="none" stroke="#2c8c63" strokeWidth="1.2" />
      {veins.map((a) => (
        <path
          key={a}
          d={`M50 50 L${(50 + 40 * Math.cos((a * Math.PI) / 180)).toFixed(1)} ${(50 + 40 * Math.sin((a * Math.PI) / 180)).toFixed(1)}`}
          stroke="#2c8c63"
          strokeWidth="1"
          opacity="0.7"
        />
      ))}
    </svg>
  )
}

export default function Pond() {
  const canvas = useRef(null)

  useEffect(() => {
    const c = canvas.current
    const ctx = c.getContext('2d')
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const rings = []
    let raf = 0
    let dpr = 1

    const size = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 2)
      c.width = window.innerWidth * dpr
      c.height = window.innerHeight * dpr
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
    }
    size()

    const draw = (now) => {
      ctx.clearRect(0, 0, c.width, c.height)
      for (let i = rings.length - 1; i >= 0; i--) {
        const r = rings[i]
        const k = (now - r.t0) / r.life
        if (k >= 1) {
          rings.splice(i, 1)
          continue
        }
        const ease = 1 - (1 - k) ** 3
        for (let j = 0; j < r.waves; j++) {
          const kk = ease - j * 0.16
          if (kk <= 0) continue
          ctx.beginPath()
          ctx.ellipse(r.x, r.y, r.max * kk, r.max * kk * 0.62, 0, 0, Math.PI * 2)
          ctx.strokeStyle = RING + (r.alpha * (1 - k) * (1 - j * 0.3)).toFixed(3) + ')'
          ctx.lineWidth = 1.4
          ctx.stroke()
        }
      }
      raf = rings.length ? requestAnimationFrame(draw) : 0
    }

    const ripple = (x, y, max, alpha, waves, life) => {
      rings.push({ x, y, max, alpha, waves, life, t0: performance.now() })
      if (rings.length > 40) rings.shift()
      if (!raf) raf = requestAnimationFrame(draw)
    }

    let lx = -999
    let ly = -999
    let lt = 0
    const move = (e) => {
      if (still || e.pointerType === 'touch') return
      const now = performance.now()
      if (now - lt < 70 || Math.hypot(e.clientX - lx, e.clientY - ly) < 36) return
      lx = e.clientX
      ly = e.clientY
      lt = now
      ripple(lx, ly, 46, 0.22, 2, 1300)
    }
    const down = (e) => ripple(e.clientX, e.clientY, 130, 0.4, 3, 1800)

    let rain = 0
    const drop = () => {
      if (!document.hidden) {
        ripple(Math.random() * window.innerWidth, Math.random() * window.innerHeight, 70, 0.18, 2, 2200)
      }
      rain = setTimeout(drop, 2200 + Math.random() * 3200)
    }
    if (!still) rain = setTimeout(drop, 1500)

    window.addEventListener('resize', size)
    window.addEventListener('pointermove', move, { passive: true })
    window.addEventListener('pointerdown', down, { passive: true })
    return () => {
      cancelAnimationFrame(raf)
      clearTimeout(rain)
      window.removeEventListener('resize', size)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerdown', down)
    }
  }, [])

  return (
    <div className="bg" aria-hidden="true">
      <div className="blob blob--1" />
      <div className="blob blob--2" />
      <div className="blob blob--3" />
      <LilyPad className="pad--1" />
      <LilyPad className="pad--2" />
      <LilyPad className="pad--3" />
      <canvas ref={canvas} className="ripples" />
      <div className="frame" />
    </div>
  )
}
