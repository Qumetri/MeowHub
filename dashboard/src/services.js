// Service cards for the hub page. Edit, then rebuild: npm run build
//
// URLs are built from build-time environment variables so this file is not
// tied to one domain. bootstrap.sh writes dashboard/.env.local from the main
// .env; Vite picks it up automatically. Anything you hardcode here still works.
//
// status: 'live' renders a clickable card, 'soon' renders a dimmed one.

const E = import.meta.env
const BASE    = E.VITE_BASE_DOMAIN   || 'localhost'
const CLOUD   = E.VITE_CLOUD_HOST    || `cloud.${BASE}`
const PHOTOS  = E.VITE_PHOTOS_HOST   || `photos.${BASE}`
const MATRIX  = E.VITE_MATRIX_HOST   || `matrix.${BASE}`
const AWG     = E.VITE_AWG_ADMIN_PATH || ''
const METUBE  = E.VITE_METUBE_PATH   || ''
const XUI_PORT = E.VITE_XUI_PANEL_PORT || '10358'
const XUI_PATH = E.VITE_XUI_PANEL_PATH || ''

export const services = [
  {
    id: 'nextcloud',
    name: 'Nextcloud',
    description: 'Files, sync & office',
    url: `https://${CLOUD}`,
    icon: 'cloud',
    gradient: ['#0082c9', '#00c6fb'],
    status: 'live',
  },
  {
    id: 'immich',
    name: 'Immich',
    description: 'Photos & videos',
    url: `https://${PHOTOS}`,
    icon: 'photo',
    gradient: ['#fa2921', '#ffb400'],
    status: 'live',
  },
  {
    id: 'plex',
    name: 'Plex',
    description: 'Movies, TV & music',
    url: 'https://app.plex.tv/desktop',
    icon: 'play',
    gradient: ['#e5a00d', '#f8d074'],
    status: 'live',
  },
  {
    id: '3xui',
    name: '3x-ui',
    description: 'Xray panel',
    url: `https://${BASE}:${XUI_PORT}/${XUI_PATH}/`,
    icon: 'shield',
    gradient: ['#7928ca', '#ff0080'],
    status: 'live',
  },
  {
    id: 'amneziawg',
    name: 'AmneziaWG',
    description: 'VPN peer manager',
    url: `https://${BASE}/${AWG}/`,
    icon: 'key',
    gradient: ['#4338ca', '#a5b4fc'],
    status: 'live',
  },
  {
    id: 'matrix',
    name: 'Matrix Chat',
    description: 'Private messenger',
    url: `https://${MATRIX}`,
    icon: 'chat',
    gradient: ['#0dbd8b', '#16d4a4'],
    status: 'live',
  },
  {
    id: 'metube',
    name: 'MeTube',
    description: 'YouTube downloader',
    url: `https://${BASE}/${METUBE}/`,
    icon: 'download',
    gradient: ['#ff0000', '#ff6d6d'],
    status: 'live',
  },
  {
    id: 'searxng',
    name: 'SearXNG',
    description: 'Private meta-search',
    url: null,
    icon: 'search',
    gradient: ['#3050ff', '#b4c2ff'],
    status: 'soon',
  },
  {
    id: 'ollama',
    name: 'Ollama',
    description: 'Local LLM API',
    url: null,
    icon: 'sparkles',
    gradient: ['#10b981', '#a7f3d0'],
    status: 'soon',
  },
  {
    id: 'outline',
    name: 'Outline VPN',
    description: 'Shadowsocks server',
    url: null,
    icon: 'key',
    gradient: ['#00bfa5', '#64ffda'],
    status: 'soon',
  },
  {
    id: 'samba',
    name: 'Samba',
    description: 'LAN file share',
    url: null,
    icon: 'folder',
    gradient: ['#64748b', '#cbd5e1'],
    status: 'soon',
  },
]
