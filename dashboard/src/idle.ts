// Idle mode: the pond's decorations are transform/opacity only, but while any of them runs the browser
// still composites a full frame 60 times a second, which keeps a laptop's GPU busy (and warm) for as long
// as the tab sits open. After IDLE_MS without input, or while the window is in the background, every
// running animation is paused (CSS ones through html[data-idle] in index.css, Web Animations such as the
// parade through the Animation API) and Stats polls less often. Any input resumes exactly where it stopped.
// Measured 2026-11-10: see the comment above `html[data-idle]` in index.css.

const IDLE_MS = 30_000
const ACTIVITY = ['pointermove', 'pointerdown', 'keydown', 'wheel', 'scroll', 'touchstart', 'resize'] as const

type Listener = (idle: boolean) => void
const listeners = new Set<Listener>()
let idle = false
let timer = 0
let paused: Animation[] = []
let last = 0

export const isIdle = (): boolean => idle

/** Called with true when the page goes idle, false when it wakes up; returns an unsubscribe function. */
export function onIdleChange(fn: Listener): () => void {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function set(next: boolean) {
  if (next === idle) return
  idle = next
  const root = document.documentElement
  if (idle) {
    root.dataset.idle = ''
    paused = document.getAnimations().filter((a) => a.playState === 'running')
    for (const a of paused) a.pause()
  } else {
    delete root.dataset.idle
    for (const a of paused) if (a.playState === 'paused') a.play()
    paused = []
  }
  for (const fn of listeners) fn(idle)
}

function wake() {
  // pointermove fires constantly: while awake, re-arm the timer at most once a second
  const now = performance.now()
  if (!idle && now - last < 1000) return
  last = now
  window.clearTimeout(timer)
  if (document.hidden) { set(true); return }
  set(false)
  timer = window.setTimeout(() => set(true), IDLE_MS)
}

export function startIdleWatch(): void {
  for (const ev of ACTIVITY) window.addEventListener(ev, wake, { passive: true, capture: true })
  window.addEventListener('focus', wake)
  window.addEventListener('blur', () => { window.clearTimeout(timer); set(true) })
  document.addEventListener('visibilitychange', () => (document.hidden ? set(true) : wake()))
  wake()
}
