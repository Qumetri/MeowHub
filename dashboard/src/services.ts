// Service cards for the hub page. Edit, then rebuild: npm run build
//
// URLs are built from build-time environment variables so this file is not
// tied to one domain. bootstrap.sh writes dashboard/.env.local from the main
// .env; Vite picks it up automatically. Anything you hardcode here still works.
//
// status: 'live' renders a clickable card, 'soon' renders a dimmed one.

import type { IconName } from './Icon'

export interface Service {
  id: string
  name: string
  description: string
  url: string
  icon: IconName
  /** [from, to] colours of the card's light */
  gradient: [string, string]
  status: 'live' | 'soon'
  section: SectionId
  /** shorter name for the phone layout, where a card is a third of the screen */
  short?: string
  /** works best with the phone turned sideways: phones show a small rotate icon on the card */
  landscape?: boolean
}

export type SectionId = 'projects' | 'services' | 'admin'

/** Page order of the sections; each card says which one it belongs to. Empty sections are not shown. */
export const sections: { id: SectionId; title: string; hint: string; hidden?: boolean }[] = [
  { id: 'projects', title: 'My projects', hint: 'Things I built, running here' },
  { id: 'services', title: 'Self-hosted services', hint: 'Everyday tools on the home server' },
  // Admin panels are not on the page: their cards live in admin.ts and the helper bot's /admin hands out a signed link.
  { id: 'admin', title: 'Admin panels', hint: 'Keys, bots and automations', hidden: true },
]

const E = import.meta.env
const BASE    = E.VITE_BASE_DOMAIN   || 'localhost'
const CLOUD   = E.VITE_CLOUD_HOST    || `cloud.${BASE}`
const PHOTOS  = E.VITE_PHOTOS_HOST   || `photos.${BASE}`
const MATRIX  = E.VITE_MATRIX_HOST   || `matrix.${BASE}`
const METUBE  = E.VITE_METUBE_PATH   || ''
const CRYPTO  = E.VITE_CRYPTO_PATH   || ''
const SENSOR  = E.VITE_SENSOR_PATH   || ''
const UPSCALE = E.VITE_UPSCALE_PATH  || ''
const PDF     = E.VITE_PDF_PATH      || ''
const VIDEO   = E.VITE_VIDEO_PATH    || ''
const HA      = E.VITE_HA_PATH       || ''

export const services: Service[] = [
  {
    // The helper bot's downloader as a page for anyone (docs/HELPER.md "Video page");
    // the owner switches it on in /services.
    id: 'video',
    name: 'Video',
    description: 'Video downloads for anyone, no login',
    url: `https://${BASE}/${VIDEO}/`,
    icon: 'film',
    gradient: ['#ff3d6e', '#ffb36b'],
    status: VIDEO ? 'live' : 'soon',
    section: 'services',
  },
  {
    id: 'metube',
    name: 'MeTube',
    description: 'YouTube downloader',
    url: `https://${BASE}/${METUBE}/`,
    icon: 'download',
    gradient: ['#ff0000', '#ff6d6d'],
    status: 'live',
    section: 'services',
  },
  {
    id: 'upscale',
    name: 'Photo Upscaler',
    description: 'Enlarge photos and pictures 2–4× with AI, compare before and after',
    url: `https://${BASE}/${UPSCALE}/`,
    icon: 'upscale',
    gradient: ['#1b5e8a', '#ffd23f'],
    status: UPSCALE ? 'live' : 'soon',
    section: 'services',
  },
  {
    id: 'pdftools',
    name: 'PDF & Docs',
    description: 'Merge, compress, convert, OCR and redact documents — files never leave the server',
    url: `https://${BASE}/${PDF}/`,
    icon: 'docs',
    gradient: ['#3a2fc7', '#ffe45c'],
    status: PDF ? 'live' : 'soon',
    section: 'services',
  },
  {
    id: 'nextcloud',
    name: 'Nextcloud',
    description: 'Files, sync & office',
    url: `https://${CLOUD}`,
    icon: 'cloud',
    gradient: ['#0082c9', '#00c6fb'],
    status: 'live',
    section: 'services',
  },
  {
    id: 'immich',
    name: 'Immich',
    description: 'Photos & videos',
    url: `https://${PHOTOS}`,
    icon: 'photo',
    gradient: ['#fa2921', '#ffb400'],
    status: 'live',
    section: 'services',
  },
  {
    id: 'plex',
    name: 'Plex',
    description: 'Movies, TV & music',
    url: 'https://app.plex.tv/desktop',
    icon: 'play',
    gradient: ['#e5a00d', '#f8d074'],
    status: 'live',
    section: 'services',
  },
  {
    id: 'matrix',
    name: 'Matrix Chat',
    description: 'Private messenger',
    url: `https://${MATRIX}`,
    icon: 'chat',
    gradient: ['#0dbd8b', '#16d4a4'],
    status: 'live',
    section: 'services',
  },
  {
    id: 'crypto',
    name: 'Crypto Tracker',
    description: 'Prices & Telegram alerts',
    url: `https://${BASE}/${CRYPTO}/`,
    icon: 'chart',
    gradient: ['#f7931a', '#fbbf24'],
    status: CRYPTO ? 'live' : 'soon',
    section: 'services',
  },
  {
    // Optional (ENABLE_HOMEASSISTANT, docs/HOMEASSISTANT.md): the path sets the gate cookie.
    id: 'homeassistant',
    name: 'Home Assistant',
    description: 'Smart home on a secret path, webhooks for ESP32s',
    url: `https://${BASE}/${HA}/`,
    icon: 'home',
    gradient: ['#18bcf2', '#7ee0ff'],
    status: HA ? 'live' : 'soon',
    section: 'services',
  },
  {
    id: 'sensor',
    name: 'Room Sensor',
    description: 'ESP32 temperature & humidity',
    url: `https://${BASE}/${SENSOR}/`,
    icon: 'thermometer',
    gradient: ['#e4572e', '#3b82f6'],
    status: SENSOR ? 'live' : 'soon',
    section: 'projects',
  },
]
