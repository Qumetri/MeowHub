// Shapes of the helper backend's JSON (helper/app/webapp.py), as the screens use them.
// mock.ts serves the same shapes in dev and in the owner's preview, so it is typed against
// this file too: a field renamed here shows up as a compile error in both places.
import type { IconName } from './icons'

export type Lang = 'ru' | 'en'
export type LangPref = 'auto' | Lang
export type Role = 'owner' | 'member' | 'stranger'
export type MemberStatus = 'active' | 'expired' | 'suspended'
/** uiState(): `soon` is an active membership with ≤ 3 days left */
export type UiState = MemberStatus | 'soon'
export type Tone = 'accent' | 'ok' | 'warn' | 'bad' | 'mute'

export interface TgUser { id: number; first_name: string; last_name?: string; username?: string; language_code?: string }

/** Anything personName()/initials()/Avatar can draw. */
export interface Person { id?: number; first_name?: string; last_name?: string; username?: string }

export interface Member extends Person {
  id: number
  lang?: string
  status: MemberStatus
  expires_ts: number | null
  days_left: number | null
  services: string[]
  /** grants plus switched-on `all`-mode services (server-side) */
  effective_services?: string[]
  created_ts: number
  last_seen_ts: number
  note: string
  vpn_email?: string
  has_vpn_client?: boolean
  matrix?: string[]
}

export interface MeService { id: string; name: string; description: string; available: boolean }

export interface Me {
  role: Role
  mode: 'tg' | 'browser'
  user: TgUser
  lang: Lang
  lang_pref: LangPref
  member: Member | null
  contact: string
  bot_username: string
  via: 'helper' | 'member' | 'browser'
  member_bot_username: string
  can_preview: boolean
  trial?: { state: 'ok' | 'used' | string; days: number }
  services: MeService[]
  /** paid extension offer; null while the owner has both providers off */
  pay?: PayOffer | null
}

// ---- payments (helper/app/payments.py)
export type PlanId = 'm1' | 'm3' | 'm12'
export type PayProvider = 'stars' | 'crypto'
export interface PayPlan { id: PlanId; days: number; stars: number; usd: number }
export interface PayOffer { stars: boolean; crypto: boolean; plans: PayPlan[] }
export type PayInvoice = { provider: 'stars'; link: string } | { provider: 'crypto'; url: string }
export interface PayCheck { paid: number; member: Member | null }
export interface PaymentRow { id: string; uid: number; name?: string; provider: PayProvider; plan: PlanId; days: number; amount: string; ts: number; refunded: number }
export interface PaymentsAdmin {
  settings: Record<PayProvider, { enabled: boolean; prices: Record<PlanId, number> }>
  crypto_token: boolean
  crypto_testnet: boolean
  plans: { id: PlanId; days: number }[]
  totals: Partial<Record<PayProvider, { count: number; amount: number }>>
  recent: PaymentRow[]
}

export type RedeemResult = 'new' | 'extended' | 'invalid' | 'used' | 'expired_code' | 'revoked_code' | 'rate_limited' | 'suspended'
export interface RedeemResponse { result: RedeemResult; member: Member | null; message?: string }
export interface TrialResponse { result: 'new' | 'used'; member: Member | null; message?: string }
export interface LangResponse { lang: Lang; lang_pref: LangPref }

// ---- VPN
export type LinkAction = 'copy' | 'download' | 'qr' | 'telegram'
export interface VpnLink { name: string; url: string; action?: LinkAction; file_name?: string; text?: string }
export interface VpnGroup { id: string; title: string; apps: string[]; hint: string; links: VpnLink[] }
export interface VpnApp { id: string; name: string; platforms: string[] | string; recommended?: boolean; go_url: string }
export interface Traffic { up: number; down: number }
export interface VpnInfo {
  sub_url?: string
  links?: VpnLink[]
  groups?: VpnGroup[]
  traffic?: Traffic | null
  online?: boolean
  apps?: VpnApp[]
}

// ---- Matrix
export interface MatrixAccount { mxid: string; created_ts: number; locked: boolean }
export interface MatrixInfo {
  server_name: string
  client_url: string
  element_url?: string
  accounts: MatrixAccount[]
  max: number
  can_create: boolean
}

// ---- downloads
export type DlStatus = 'queued' | 'running' | 'done' | 'error' | 'canceled' | 'expired'
export interface DlFile { name: string; size: number; url: string; kind: 'video' | 'audio' | 'subs' | string; lang?: string }
export interface DlJob {
  id: number
  url: string
  preset: string
  preset_label?: string
  title: string | null
  status: DlStatus
  percent: number | null
  size: number | null
  /** seconds, or a ready-made string from an older server */
  eta: number | string | null
  error: string
  created_ts: number
  finished_ts: number | null
  expires_ts: number | null
  files: DlFile[]
}
export interface DlPreset { id: string; label: string }
export interface DlExtras { subs?: string[]; clip?: boolean; playlist_max?: number }
export interface DlPresets {
  video?: DlPreset[]
  audio?: DlPreset[]
  extras?: DlExtras
  limits?: { active: number; per_hour: number; per_day: number; ttl_min: number }
  sites?: string[]
}
/** owner view: the jobs of everyone */
export interface DlAdminJob extends DlJob { uid?: number; user?: string | { name?: string }; user_name?: string }
export interface DlAdmin {
  disk_free_gb: number | null
  active: number
  today?: { jobs?: number; bytes?: number }
  jobs?: DlAdminJob[]
  items?: DlAdminJob[]
}

// ---- audiobooks
export type BookStatus = 'preparing' | 'ready' | 'queued' | 'narrating' | 'assembling' | 'done' | 'error' | 'cancelled'
export interface BookVoice { key: string; engine: string; id: string; name: string; gender: 'f' | 'm'; accent: string }
export interface MemberBook {
  id: number
  title: string
  author: string
  lang: Lang
  status: BookStatus
  voice: string | null
  chapters: number
  listen_s: number
  has_cover: boolean
  percent?: number
  eta?: number | null
  seconds?: number
  size?: number
  error?: string
  audio_url?: string
  save_url?: string
  /** engine -> seconds the whole book would take */
  takes?: Record<string, number>
  wait?: { ahead: number; starts_in: number; takes: number }
}
export interface Quota { per_user?: number; per_day?: number; used: number; left: number; max_mb: number; unlimited: boolean; next_in?: number | null }
export interface BooksView {
  books: MemberBook[]
  voices: Partial<Record<Lang, BookVoice[]>>
  queue: { books: number; wait: number } | null
  limits?: { per_user: number; max_mb: number }
  quota: Quota & { per_user: number }
}

// ---- photo upscaling
export type UpStatus = 'queued' | 'running' | 'done' | 'error' | 'cancelled'
export interface UpscaleJob {
  id: number
  name: string
  preset: string
  status: UpStatus
  progress: number
  scale: number
  scale_asked: number
  format: string
  in_w: number
  in_h: number
  out_w: number
  out_h: number
  out_size?: number
  took?: number
  view_url?: string
  save_url?: string
  wait?: { ahead: number; ready_in: number }
  /** preview only: inline pictures instead of authed URLs */
  demo?: { before: string; after?: string }
}
export interface UpscaleView {
  jobs: UpscaleJob[]
  presets: string[]
  queue: { jobs: number; wait: number } | null
  online?: boolean
  limits?: { per_day: number; active: number; max_mb: number }
  quota: Quota & { per_day: number }
}

// ---- admin
export interface MemberRow extends Member { traffic: Traffic | null; online: boolean; messages_30d?: number; app_30d?: number }
/** GET admin/members: an array that may carry the X-Warning header as `.warning` */
export type MemberList = MemberRow[] & { warning?: string }
export interface MemberEvent { id?: number; ts: number; uid: number; kind: string; detail?: string }
export interface MemberFull extends MemberRow {
  vpn_links_count?: number | null
  matrix_accounts?: MatrixAccount[]
  events?: MemberEvent[]
}
export type MemberAction =
  | { action: 'extend'; days: number }
  | { action: 'set_expiry'; ts: number | null }
  | { action: 'suspend' | 'resume' | 'delete' }
  | { action: 'services'; services: string[] }
  | { action: 'note'; note: string }

export type CodeState = 'live' | 'used' | 'expired' | 'revoked'
export interface AccessCode {
  code: string
  days: number
  services: string[]
  uses_max: number
  uses: number
  created_ts: number
  valid_until: number
  revoked: number
  note: string
  state: CodeState
  share_url: string
}
export interface NewCode { code: string; share_url: string }

export interface Inbound { id: number; remark: string; protocol: string; port: number; enable: boolean; member: boolean }
export interface SyncState { ts?: number; ok?: boolean; error?: string; vpn_clients?: number }
export interface ActivityDay { day: string; users: number; messages: number; app: number }
export type KpiKey = 'members' | 'active' | 'expired' | 'suspended' | 'expiring_7d'
export interface Overview {
  counts: Record<KpiKey, number>
  activity: ActivityDay[]
  sync: SyncState
  vpn_configured: boolean
  matrix_configured: boolean
  can_tokens?: boolean
}
export interface AdminService { id: string; name: string; description: string; mode: 'grant' | 'all' | string; enabled: boolean; members_with_access?: number }
export interface TrialSettings { enabled: boolean; days: number; per_day: number; started: number }

export type KeyId = 'helper' | 'member' | 'crypto' | 'xui'
export interface KeyItem {
  id: KeyId
  kind: 'bot' | 'api'
  configured: boolean
  source: 'env' | 'page' | 'ctl' | 'none'
  masked: string
  updated_ts?: number | null
  restart_on_change?: boolean
  bot?: { id: number; username: string; name: string; ok: boolean; error: string; avatar_url?: string } | null
  check?: { ok: boolean; error: string; detail?: string }
  note?: string
}
export interface KeySaved { item: KeyItem; restarting: boolean }

// ---- navigation (App.tsx / Preview.tsx)
export type Screen = 'vpn' | 'matrix' | 'code' | 'downloads' | 'help' | 'books' | 'upscale' | 'member'
export type TabId = 'members' | 'codes' | 'overview' | 'me'
export interface StackEntry { s: Screen; p: { uid: number } | null }
export interface Go {
  (screen: 'tab', tab: TabId): void
  (screen: Screen, params?: { uid: number } | null, replace?: boolean): void
}

export interface AppContext {
  me: Me
  go: Go
  back: () => void
  depth: number
  mode: 'tg' | 'browser'
  version: number
  bump: () => void
  refreshMe: () => void
  adminFilter: string
  setAdminFilter: (f: string) => void
  openPreview?: (() => void) | null
  lang: Lang
  langPref: LangPref
  setLangPref: (pref: LangPref) => void
}

export type { IconName }
