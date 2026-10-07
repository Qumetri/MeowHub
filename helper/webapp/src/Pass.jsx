import { PawShapes } from './icons.jsx'
import { t } from './i18n.js'
import { daysWord, fmtDate, personName, uiState } from './util.js'

// The one bold element: a Wallet-style card whose fill follows the status.
export default function Pass({ member, name }) {
  const st = uiState(member)
  const forever = member.expires_ts == null
  const inactive = st === 'expired' || st === 'suspended'
  const big = inactive ? (st === 'expired' ? '0' : '—') : forever ? '∞' : String(member.days_left)
  const unit = inactive ? daysWord(0) : forever ? t('pass.no_expiry') : daysWord(member.days_left)
  let line
  if (st === 'suspended') line = t('pass.suspended')
  else if (st === 'expired') line = t('pass.expired', { date: fmtDate(member.expires_ts) })
  else if (forever) line = t('pass.no_expiry')
  else line = t('pass.active_until', { date: fmtDate(member.expires_ts) })

  return (
    <div className={'pass pass--' + st} role="group" aria-label={`MeowHub — ${line}`}>
      <svg className="pass__paws" aria-hidden="true" width="100%" height="100%">
        <defs>
          <pattern id="paws" width="64" height="64" patternUnits="userSpaceOnUse" patternTransform="rotate(-18)">
            <g transform="translate(6 6) scale(1.1)"><PawShapes /></g>
            <g transform="translate(40 36) scale(0.9) rotate(14 12 12)"><PawShapes /></g>
          </pattern>
          <linearGradient id="paw-fade" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#fff" stopOpacity="0.15" />
            <stop offset="1" stopColor="#fff" stopOpacity="1" />
          </linearGradient>
          <mask id="paw-mask"><rect width="100%" height="100%" fill="url(#paw-fade)" /></mask>
        </defs>
        <rect width="100%" height="100%" fill="url(#paws)" mask="url(#paw-mask)" />
      </svg>
      <div className="pass__sheen" aria-hidden="true" />
      <div className="pass__top">
        <span className="pass__brand">MeowHub</span>
        <span className="pass__name">{name || personName(member)}</span>
      </div>
      <div className="pass__mid">
        <span className={'pass__num' + (big.length > 3 ? ' sm' : '')}>{big}</span>
        <span className="pass__unit">{unit}</span>
      </div>
      <div className="pass__line">{line}</div>
    </div>
  )
}
