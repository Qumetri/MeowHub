// One stroke style for every icon: 24px grid, 1.8 stroke, round caps/joins.
const P = {
  shield: ['M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z', 'M9 12l2 2 4-4'],
  chat: ['M20 12a8 8 0 0 1-11.6 7.1L4 20l1-4.1A8 8 0 1 1 20 12z'],
  calplus: ['M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z', 'M4 10h16M8 3v4M16 3v4M12 13v4M10 15h4'],
  help: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M9.6 9.6a2.5 2.5 0 1 1 3.6 2.2c-.8.4-1.2.9-1.2 1.7', 'M12 17h.01'],
  copy: ['M9 9h10v11H9z', 'M5 15V4h10'],
  check: ['M5 12.5l4.5 4.5L19 7.5'],
  qr: ['M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4z', 'M14 14h2v2h-2zM18 14h2v2M14 18h2v2h4v-2'],
  chevron: ['M9 6l6 6-6 6'],
  chevdown: ['M6 9l6 6 6-6'],
  chevleft: ['M15 6l-6 6 6 6'],
  external: ['M14 4h6v6M20 4l-9 9', 'M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5'],
  key: ['M8 19a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M11 12l9-9M16 7l3 3M14 9l2 2'],
  user: ['M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M4 20c0-3.8 3.6-6 8-6s8 2.2 8 6'],
  users: ['M9 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8z', 'M2.5 20c0-3.5 2.9-5.5 6.5-5.5s6.5 2 6.5 5.5', 'M16 4.3a4 4 0 0 1 0 7.4M18 14.8c2 .6 3.5 2.2 3.5 5.2'],
  ticket: ['M3 8a1 1 0 0 1 1-1h16a1 1 0 0 1 1 1v2.5a1.5 1.5 0 0 0 0 3V16a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1v-2.5a1.5 1.5 0 0 0 0-3z', 'M14 7v2M14 11v2M14 15v2'],
  chart: ['M5 20v-9M12 20V5M19 20v-12'],
  refresh: ['M20 11a8 8 0 0 0-14.5-4M4 4v4h4', 'M4 13a8 8 0 0 0 14.5 4M20 20v-4h-4'],
  trash: ['M4 7h16M9 7V4h6v3', 'M6 7l1 13h10l1-13M10 11v6M14 11v6'],
  pause: ['M8 5v14M16 5v14'],
  play: ['M8 5l11 7-11 7z'],
  lock: ['M6 11h12v9H6z', 'M8 11V8a4 4 0 0 1 8 0v3'],
  send: ['M21 3L10 14', 'M21 3l-6.5 18-4-7.5L3 9.5z'],
  eye: ['M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z'],
  eyeoff: ['M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z', 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z', 'M4 4l16 16'],
  search: ['M11 17.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13z', 'M16 16l4.5 4.5'],
  clock: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 7v5l3 2'],
  globe: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18'],
  server: ['M5 4h14a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z', 'M5 14h14a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-4a1 1 0 0 1 1-1z', 'M8 7h.01M8 17h.01'],
  up: ['M12 19V5M6 11l6-6 6 6'],
  down: ['M12 5v14M6 13l6 6 6-6'],
  info: ['M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z', 'M12 11v5M12 8h.01'],
  warning: ['M12 4l9.5 16h-19z', 'M12 10v4M12 17h.01'],
  x: ['M6 6l12 12M18 6L6 18'],
  plus: ['M12 5v14M5 12h14'],
  pencil: ['M4 20h4L19 9l-4-4L4 16z'],
  tools: ['M14.7 6.3a4 4 0 0 0-5 5L4 17l3 3 5.7-5.7a4 4 0 0 0 5-5l-2.4 2.4-2.6-.6-.6-2.6z'],
  home: ['M4 11l8-7 8 7', 'M6 10v10h12V10'],
  bot: ['M7 8h10a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z', 'M12 4v4M9 13h.01M15 13h.01M9.5 16.5h5'],
  calendar: ['M5 5h14a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1z', 'M4 10h16M8 3v4M16 3v4'],
  infinity: ['M7 8c-2.8 0-4 1.8-4 4s1.2 4 4 4c4 0 6-8 10-8 2.8 0 4 1.8 4 4s-1.2 4-4 4c-4 0-6-8-10-8z'],
  link: ['M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1', 'M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1'],
  download: ['M12 4v11M7 11l5 5 5-5', 'M5 20h14'],
  film: ['M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z', 'M8 4v16M16 4v16M4 9h4M16 9h4M4 15h4M16 15h4'],
  music: ['M9 18V6l10-2v12', 'M9 18a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z', 'M19 16a2.5 2.5 0 1 1-5 0 2.5 2.5 0 0 1 5 0z'],
  clipboard: ['M9 4h6v3H9z', 'M8 5.5H6a1 1 0 0 0-1 1V20a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V6.5a1 1 0 0 0-1-1h-2'],
  sliders: ['M4 7h9M17 7h3M4 17h3M11 17h9', 'M15 7m-2 0a2 2 0 1 0 4 0 2 2 0 1 0-4 0', 'M9 17m-2 0a2 2 0 1 0 4 0 2 2 0 1 0-4 0'],
  moon: ['M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z'],
}

export default function Icon({ name, size = 20, className, strokeWidth = 1.8 }) {
  const paths = P[name]
  if (!paths) return null
  return (
    <svg className={className} viewBox="0 0 24 24" width={size} height={size} fill="none"
      stroke="currentColor" strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths.map((d, i) => <path key={i} d={d} />)}
    </svg>
  )
}

// Filled paw print (brand mark / pattern). 24px box.
export function PawShapes() {
  return (
    <g>
      <ellipse cx="12" cy="16.2" rx="5.2" ry="4.2" />
      <circle cx="5.2" cy="10.8" r="2.1" />
      <circle cx="9.4" cy="6" r="2.3" />
      <circle cx="14.6" cy="6" r="2.3" />
      <circle cx="18.8" cy="10.8" r="2.1" />
    </g>
  )
}
export function Paw({ size = 24, className }) {
  return (
    <svg className={className} viewBox="0 0 24 24" width={size} height={size} fill="currentColor" aria-hidden="true">
      <PawShapes />
    </svg>
  )
}
