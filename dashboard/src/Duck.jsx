// The hub's mascot: a rubber duck with cat ears (Meow + Duck). One SVG, drawn
// facing right, used everywhere — the hero, the parade along the edges and the
// heads that peek over the cards. Pupils read --px/--py (−1…1) from CSS so a
// parent can make the eyes follow the pointer without re-rendering.
export default function Duck({ ears = true, className = '', size = 64, body = true }) {
  return (
    <svg
      className={`duck ${className}`}
      viewBox={body ? '0 0 64 52' : '28 0 36 34'}
      width={size}
      height={body ? (size * 52) / 64 : size * (34 / 36)}
      aria-hidden="true"
    >
      {body && (
        <>
          <path d="M12 37 Q3 26 13 27 Q16 31 18 33 Z" fill="url(#duck-body)" />
          <ellipse cx="30" cy="38" rx="21" ry="12" fill="url(#duck-body)" />
          <path className="duck__wing" d="M18 36 Q29 29 38 36 Q30 45 19 41 Z" fill="#f5a20f" opacity="0.75" />
        </>
      )}
      {ears && (
        <g className="duck__ears">
          <path d="M35.5 15 L37 3.5 L44 11 Z" fill="url(#duck-body)" />
          <path d="M37.4 12.5 L38.2 6.8 L41.6 10.6 Z" fill="#ff9fb8" />
          <path d="M45 10 L50.5 0.5 L53 11.5 Z" fill="url(#duck-body)" />
          <path d="M47.2 9.6 L50 4.6 L51.2 10.4 Z" fill="#ff9fb8" />
        </g>
      )}
      <circle cx="44" cy="21" r="11.5" fill="url(#duck-body)" />
      <circle cx="44.5" cy="26" r="2.6" fill="#ff8fab" opacity="0.55" />
      <path d="M53.5 21 Q63.5 21.5 62.5 25.5 Q57 29 52.5 26 Z" fill="url(#duck-beak)" />
      <circle cx="48" cy="17.5" r="3.6" fill="#fff" />
      <circle className="duck__pupil" cx="48.6" cy="17.7" r="2" fill="#1b1530" />
      <circle cx="49.3" cy="16.8" r="0.7" fill="#fff" />
    </svg>
  )
}

// Gradients live once per page: duplicate ids across many inline SVGs break in
// Chrome as soon as the first copy is inside a hidden subtree.
export function DuckDefs() {
  return (
    <svg width="0" height="0" style={{ position: 'absolute' }} aria-hidden="true">
      <defs>
        <linearGradient id="duck-body" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#ffe36e" />
          <stop offset="1" stopColor="#ffb21e" />
        </linearGradient>
        <linearGradient id="duck-beak" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#ffa43a" />
          <stop offset="1" stopColor="#ff6a1f" />
        </linearGradient>
      </defs>
    </svg>
  )
}
