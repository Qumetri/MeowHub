import { useEffect, useRef } from 'react'

// The page background is a pond at night: the old gradient blobs are now
// light on the water, a few lily pads drift, and the surface ripples where the
// pointer moves or taps (and, now and then, under a raindrop).
//
// Ripples are short-lived DOM rings animated with transform + opacity only, so
// the compositor plays them without repainting anything. (A canvas did this
// first, but wherever it isn't GPU-backed — headless, many phones — every
// ripple frame repainted the whole document.)

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
  const water = useRef(null)

  useEffect(() => {
    const pond = water.current
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let live = 0

    // size: diameter in px at the end; waves: concentric rings; life: ms
    const ripple = (x, y, size, alpha, waves, life) => {
      if (live > 14) return
      live++
      const r = document.createElement('span')
      r.className = 'ripple'
      r.style.cssText = `left:${x}px;top:${y}px;width:${size}px;height:${size * 0.62}px;` +
        `margin:${-size * 0.31}px 0 0 ${-size / 2}px;--a:${alpha};--life:${life}ms`
      for (let j = 0; j < waves; j++) {
        const w = document.createElement('i')
        w.style.animationDelay = `${j * life * 0.14}ms`
        w.style.opacity = 0
        r.appendChild(w)
      }
      r.lastChild.addEventListener('animationend', () => {
        r.remove()
        live--
      })
      pond.appendChild(r)
    }

    let lx = -999
    let ly = -999
    let lt = 0
    const move = (e) => {
      if (still || e.pointerType === 'touch') return
      const now = performance.now()
      if (now - lt < 90 || Math.hypot(e.clientX - lx, e.clientY - ly) < 40) return
      lx = e.clientX
      ly = e.clientY
      lt = now
      ripple(lx, ly, 92, 0.22, 2, 1300)
    }
    // click, not pointerdown: on a phone every scroll starts with a pointerdown
    const tap = (e) => ripple(e.clientX, e.clientY, 260, 0.4, 3, 1800)

    let rain = 0
    const drop = () => {
      if (!document.hidden) {
        ripple(Math.random() * window.innerWidth, Math.random() * window.innerHeight, 140, 0.18, 2, 2200)
      }
      rain = setTimeout(drop, 4000 + Math.random() * 5000)
    }
    if (!still) rain = setTimeout(drop, 1500)

    window.addEventListener('pointermove', move, { passive: true })
    window.addEventListener('click', tap, { passive: true })
    return () => {
      clearTimeout(rain)
      window.removeEventListener('pointermove', move)
      window.removeEventListener('click', tap)
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
      <div ref={water} className="ripples" />
      <div className="frame">
        <i />
        <i />
        <i />
        <i />
      </div>
    </div>
  )
}
