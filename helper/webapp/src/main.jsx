import './styles.css'

async function boot() {
  // Dev-only: the fake Telegram.WebApp must exist before tg.js is evaluated,
  // hence start.jsx (which imports tg.js) is loaded dynamically after it.
  if (import.meta.env.DEV) {
    const p = new URLSearchParams(location.search)
    if (p.get('mock') && p.get('mode') === 'tg') (await import('./mock-tg.js')).installFakeTelegram(p)
  }
  await import('./start.jsx')
}
boot()
