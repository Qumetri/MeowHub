// The hub's mascot: a rubber duck with cat ears (Meow + Duck). One SVG, drawn
// facing right, used everywhere — the hero, the parade along the edges and the
// heads that peek over the cards. Pupils read --px/--py (−1…1) from CSS so a
// parent can make the eyes follow the pointer without re-rendering.
import { LOGO } from './logo'

interface DuckProps { ears?: boolean; className?: string; size?: number; body?: boolean }

export default function Duck(props: DuckProps) {
  return LOGO === 'draft' ? <DuckDraft {...props} /> : <DuckClassic {...props} />
}

// 2026-11-16 draft. What was off in the classic one, and what changed:
//  - the ears were two thin spikes of different size, leaning right -> two equal, rounded cat ears set on the head;
//  - the tail was a stub that read as a dent -> a curled cat tail (the "Meow" half of the name, visible at any size);
//  - the eye sat high and small, the cheek touched the beak -> a larger eye in the middle of the face, whiskers
//    where the cheek was, a flatter and wider bill;
//  - same box, same classes (duck__ears / duck__wing / duck__pupil), so the parade, the peeking heads and the
//    eye-follow keep working unchanged.
function DuckDraft({ ears = true, className = '', size = 64, body = true }: DuckProps) {
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
          <path d="M13 40 C4 39 1.5 29 7 24 C10 21.3 14.3 23.6 12.4 26.6 C10.2 29.6 11.6 32.6 16 33.4 Z" fill="url(#duck-body)" />
          <ellipse cx="29.5" cy="38.5" rx="20.5" ry="12" fill="url(#duck-body)" />
          <path className="duck__wing" d="M17.5 37.5 Q28 30.5 38 37 Q30.5 45.5 19.5 42.5 Z" fill="#f5a20f" opacity="0.8" />
        </>
      )}
      {ears && (
        <g className="duck__ears" stroke="url(#duck-body)" strokeWidth="1.8" strokeLinejoin="round">
          <path d="M34.6 15.2 L36 3.6 L44.4 9.8 Z" fill="url(#duck-body)" />
          <path d="M45.2 9.6 L53 3.2 L54.6 14.4 Z" fill="url(#duck-body)" />
          <path d="M37 12.2 L37.7 7.2 L41.4 10 Z" fill="#ff9fb8" stroke="none" />
          <path d="M48.2 9.9 L51.6 7 L52.3 12 Z" fill="#ff9fb8" stroke="none" />
        </g>
      )}
      <circle cx="44.5" cy="21" r="12" fill="url(#duck-body)" />
      <path d="M41.4 24.2 L35.6 22.8 M41.4 26.4 L35.8 27.4" stroke="#c77700" strokeWidth="0.9" strokeLinecap="round" opacity="0.65" fill="none" />
      <path d="M54.2 20.2 Q64.4 20 63.4 24.8 Q58 28.6 53.2 25.6 Z" fill="url(#duck-beak)" />
      <circle cx="48" cy="18.6" r="4.2" fill="#fff" />
      <circle className="duck__pupil" cx="48.8" cy="18.9" r="2.4" fill="#1b1530" />
      <circle cx="49.7" cy="17.8" r="0.85" fill="#fff" />
    </svg>
  )
}

function DuckClassic({ ears = true, className = '', size = 64, body = true }: DuckProps) {
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
