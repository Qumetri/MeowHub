// Admin panels (section 'admin'). NOT imported by the page: they are off the hub, so their secret
// URLs are not in its bundle either. vite.config.ts adds them to dist/services.json, which feeds the
// helper bot's health checks, /links (filtered) and the signed page the bot hands out with /admin.
import type { Service } from './services'

const E = import.meta.env
const BASE    = E.VITE_BASE_DOMAIN   || 'localhost'
const AWG     = E.VITE_AWG_ADMIN_PATH || ''
const N8N     = E.VITE_N8N_PATH      || ''
const BOTS    = E.VITE_BOT_ADMIN_PATH || ''
const XUI_PORT = E.VITE_XUI_PANEL_PORT || '10358'
const XUI_PATH = E.VITE_XUI_PANEL_PATH || ''

export const adminServices: Service[] = [
  {
    id: '3xui',
    name: '3x-ui',
    description: 'Xray panel',
    url: `https://${BASE}:${XUI_PORT}/${XUI_PATH}/`,
    icon: 'shield',
    gradient: ['#7928ca', '#ff0080'],
    status: 'live',
    section: 'admin',
  },
  {
    id: 'amneziawg',
    name: 'AmneziaWG',
    description: 'VPN peer manager',
    url: `https://${BASE}/${AWG}/`,
    icon: 'key',
    gradient: ['#4338ca', '#a5b4fc'],
    status: 'live',
    section: 'admin',
  },
  {
    id: 'n8n',
    name: 'n8n',
    description: 'Workflow automation',
    url: `https://${BASE}/${N8N}/`,
    icon: 'flow',
    gradient: ['#ea4b71', '#ff8da1'],
    status: N8N ? 'live' : 'soon',
    section: 'admin',
  },
  {
    id: 'bots',
    name: 'Bots',
    description: 'Members, codes & bot stats',
    url: `https://${BASE}/${BOTS}/`,
    icon: 'bot',
    gradient: ['#229ed9', '#7dd3fc'],
    status: BOTS ? 'live' : 'soon',
    section: 'admin',
  },
]
