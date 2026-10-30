// The round rubber stamp: on the front page it states the promise, on a result it marks it done.
// Text runs along a circle (SVG textPath), the way a real office seal reads.
export default function Seal({ ring, center, sub, size = 132, className = '' }) {
  const id = `seal-${ring.length}-${center.length}`
  return (
    <svg className={`seal ${className}`} width={size} height={size} viewBox="0 0 200 200" role="img" aria-label={`${center}. ${ring}`}>
      <defs>
        <path id={id} d="M100,100 m-74,0 a74,74 0 1,1 148,0 a74,74 0 1,1 -148,0" />
      </defs>
      <circle cx="100" cy="100" r="94" fill="none" stroke="currentColor" strokeWidth="5" />
      <circle cx="100" cy="100" r="58" fill="none" stroke="currentColor" strokeWidth="2.5" />
      <text className="seal-ring" fill="currentColor">
        <textPath href={`#${id}`} startOffset="0" textLength="458" lengthAdjust="spacing">{ring}</textPath>
      </text>
      <text x="100" y={sub ? 99 : 109} textAnchor="middle" className="seal-center" fill="currentColor"
        textLength={center.length > 4 ? 100 : undefined} lengthAdjust="spacingAndGlyphs">{center}</text>
      {sub && <text x="100" y="124" textAnchor="middle" className="seal-sub" fill="currentColor">{sub}</text>}
    </svg>
  )
}
