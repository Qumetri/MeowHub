import { motion } from 'framer-motion'
import Icon from './Icon.jsx'
import Stats from './Stats.jsx'
import { services } from './services.js'

const container = {
  hidden: {},
  show: { transition: { staggerChildren: 0.07, delayChildren: 0.15 } },
}

const item = {
  hidden: { opacity: 0, y: 24, scale: 0.97 },
  show: {
    opacity: 1,
    y: 0,
    scale: 1,
    transition: { type: 'spring', stiffness: 260, damping: 24 },
  },
}

function Card({ service }) {
  const live = service.status === 'live'
  const [from, to] = service.gradient
  const Tag = live ? motion.a : motion.div

  return (
    <Tag
      className={`card ${live ? '' : 'card--soon'}`}
      variants={item}
      whileHover={live ? { y: -5, scale: 1.02 } : undefined}
      whileTap={live ? { scale: 0.98 } : undefined}
      {...(live && { href: service.url, target: '_blank', rel: 'noreferrer' })}
      style={{ '--from': from, '--to': to }}
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
          <span className="dot" /> live
        </span>
      ) : (
        <span className="badge badge--soon">coming soon</span>
      )}
      {live && (
        <svg className="card__arrow" viewBox="0 0 24 24" width="18" height="18"
          fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M7 17 17 7M9 7h8v8" />
        </svg>
      )}
    </Tag>
  )
}

export default function App() {
  return (
    <>
      <div className="bg" aria-hidden="true">
        <div className="blob blob--1" />
        <div className="blob blob--2" />
        <div className="blob blob--3" />
      </div>

      <main>
        <motion.header
          initial={{ opacity: 0, y: -16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, ease: 'easeOut' }}
        >
          <div className="logo">🐱🦆</div>
          <h1>
            {(import.meta.env.VITE_HUB_NAME || 'Home').split(' ')[0]}{' '}
            <span className="grad">{(import.meta.env.VITE_HUB_NAME || 'Home Hub').split(' ').slice(1).join(' ') || 'Hub'}</span>
          </h1>
          <p className="tagline">your self-hosted corner of the internet</p>
        </motion.header>

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

        <motion.footer
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 1, duration: 0.8 }}
        >
          {import.meta.env.VITE_BASE_DOMAIN || 'localhost'} · served by caddy
        </motion.footer>
      </main>
    </>
  )
}
