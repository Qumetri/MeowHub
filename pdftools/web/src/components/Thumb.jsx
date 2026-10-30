import { useEffect, useRef, useState } from 'react'
import { renderPage } from '../lib/pdf.js'

// A page thumbnail that renders only once it scrolls near the viewport.
export default function Thumb({ doc, n, rotation = 0, width = 132 }) {
  const box = useRef(null)
  const [seen, setSeen] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    const el = box.current
    if (!el) return
    const io = new IntersectionObserver(([e]) => e.isIntersecting && setSeen(true), { rootMargin: '400px' })
    io.observe(el)
    return () => io.disconnect()
  }, [])

  useEffect(() => {
    if (!seen || !doc) return
    let alive = true
    renderPage(doc, n, width, rotation)
      .then((canvas) => {
        if (!alive || !box.current) return
        canvas.className = 'thumb-canvas'
        box.current.replaceChildren(canvas)
      })
      .catch(() => alive && setFailed(true))
    return () => { alive = false }
  }, [seen, doc, n, rotation, width])

  return <div ref={box} className="thumb-box">{failed && <span className="thumb-failed">не открылась</span>}</div>
}
