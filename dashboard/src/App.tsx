import { useEffect, useRef, useState, type CSSProperties, type MouseEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { motion, type Variants } from 'framer-motion'
import Duck, { DuckDefs } from './Duck'
import Icon from './Icon'
import Parade from './Parade'
import Pond from './Pond'
import Stats from './Stats'
import { services, type Service } from './services'

const container: Variants = {
  hidden: {},
  show: { transition: { staggerChildren: 0.06, delayChildren: 0.35 } },
}

const item: Variants = {
  hidden: { opacity: 0, y: 28, rotate: -1.5 },
  show: {
    opacity: 1,
    y: 0,
    rotate: 0,
    transition: { type: 'spring', stiffness: 220, damping: 18 },
  },
}

const NAME = import.meta.env.VITE_HUB_NAME || 'Home Hub'
const DOMAIN = import.meta.env.VITE_BASE_DOMAIN || 'localhost'

const fine = typeof window !== 'undefined' && window.matchMedia('(hover: hover) and (pointer: fine)').matches
const still = typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

// Pointer position inside an element, as −1…1 on each axis.
function aim(e: { clientX: number; clientY: number }, el: Element): [x: number, y: number, rect: DOMRect] {
  const r = el.getBoundingClientRect()
  return [((e.clientX - r.left) / r.width) * 2 - 1, ((e.clientY - r.top) / r.height) * 2 - 1, r]
}

function Card({ service }: { service: Service }) {
  const live = service.status === 'live'
  const [from, to] = service.gradient
  const ref = useRef<HTMLDivElement>(null)
  const Tag = live ? 'a' : 'div'

  // Tilt toward the pointer, move the light, and let the peeking duck look at
  // it. Written as CSS variables so hovering never re-renders React.
  const onMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const el = ref.current
    if (!fine || !el) return
    const [x, y] = aim(e, el)
    el.style.setProperty('--mx', `${((x + 1) / 2) * 100}%`)
    el.style.setProperty('--my', `${((y + 1) / 2) * 100}%`)
    el.style.setProperty('--px', x.toFixed(3))
    el.style.setProperty('--py', y.toFixed(3))
    if (!still) {
      el.style.setProperty('--tilt', `perspective(900px) rotateX(${(-y * 6).toFixed(2)}deg) rotateY(${(x * 7).toFixed(2)}deg)`)
    }
  }
  const onLeave = () => {
    const el = ref.current
    if (!el) return
    ;['--tilt', '--px', '--py'].forEach((v) => el.style.removeProperty(v))
  }
  // A splash from where the card was tapped (on click, so starting a scroll on
  // a card doesn't splash).
  const onTap = (e: MouseEvent<HTMLDivElement>) => {
    const el = ref.current?.querySelector('.card')
    if (!el) return
    const r = el.getBoundingClientRect()
    const s = document.createElement('span')
    s.className = 'splash'
    s.style.left = `${e.clientX - r.left}px`
    s.style.top = `${e.clientY - r.top}px`
    el.appendChild(s)
    s.addEventListener('animationend', () => s.remove())
  }

  return (
    <motion.div
      ref={ref}
      className={`tile ${live ? '' : 'tile--soon'}`}
      variants={item}
      style={{ '--from': from, '--to': to } as CSSProperties}
      onPointerMove={onMove}
      onPointerLeave={onLeave}
      onClick={live ? onTap : undefined}
    >
      <span className="peek" aria-hidden="true">
        <Duck body={false} size={40} />
      </span>
      <Tag
        className="card"
        {...(live && { href: service.url, target: '_blank', rel: 'noreferrer' })}
      >
        <div className="card__icon">
          <Icon name={service.icon} />
        </div>
        <div className="card__body">
          <h2>{service.name}</h2>
          <p>{service.description}</p>
        </div>
        {live ? (
          <span className="badge badge--live">
            <span className="dot" /> Live
          </span>
        ) : (
          <span className="badge badge--soon">Coming soon</span>
        )}
        {live && (
          <svg className="card__arrow" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true"
            fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M7 17 17 7M9 7h8v8" />
          </svg>
        )}
      </Tag>
    </motion.div>
  )
}

// The mascot floats on its own patch of water. Its eyes follow the pointer
// anywhere on the page; pressing it quacks and sends one more duckling into
// the parade along the window's edge.
function Hero() {
  const ref = useRef<HTMLButtonElement>(null)
  const [quacks, setQuacks] = useState(0)

  useEffect(() => {
    if (!fine) return
    const look = (e: PointerEvent) => {
      const el = ref.current
      if (!el) return
      const [x, y] = aim(e, el)
      el.style.setProperty('--px', Math.max(-1, Math.min(1, x / 3)).toFixed(3))
      el.style.setProperty('--py', Math.max(-1, Math.min(1, y / 3)).toFixed(3))
    }
    window.addEventListener('pointermove', look, { passive: true })
    return () => window.removeEventListener('pointermove', look)
  }, [])

  const quack = () => {
    setQuacks((q) => q + 1)
    window.dispatchEvent(new Event('duck:add'))
    const el = ref.current
    if (!el) return
    el.classList.remove('mascot--quack')
    void el.offsetWidth
    el.classList.add('mascot--quack')
  }

  return (
    <motion.header
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.6 }}
    >
      <button ref={ref} type="button" className="mascot" onClick={quack}
        aria-label="Quack and add a duckling to the parade">
        <span className="mascot__duck">
          <Duck size={112} />
        </span>
        <span className="mascot__water" />
        <span className="mascot__bubble" key={quacks}>{quacks ? 'quack!' : ''}</span>
      </button>
      <h1 className="title" aria-label={NAME}>
        {NAME.split('').map((ch, i) => (
          <span key={i} aria-hidden="true" style={{ '--i': i } as CSSProperties}>
            {ch === ' ' ? ' ' : ch}
          </span>
        ))}
      </h1>
      <p className="tagline">
        {quacks === 0
          ? 'Everything we host at home, in one pond'
          : `${Math.min(quacks + 4, 13)} ducklings in the parade`}
      </p>
    </motion.header>
  )
}

export default function App() {
  return (
    <>
      <DuckDefs />
      <Pond />
      <Parade />

      <main>
        <Hero />

        <Stats />

        <motion.section
          className="grid"
          variants={container}
          initial="hidden"
          animate="show"
        >
          {services.map((s) => (
            <Card key={s.id} service={s} />
          ))}
        </motion.section>

        <footer>Served by Caddy from {DOMAIN}</footer>
      </main>
    </>
  )
}
