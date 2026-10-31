/// <reference types="vite/client" />

// Build-time settings: bootstrap.sh writes them to dashboard/.env.local from the main .env.
// All optional — every card falls back to a value derived from VITE_BASE_DOMAIN.
interface ImportMetaEnv {
  readonly VITE_AWG_ADMIN_PATH?: string
  readonly VITE_BASE_DOMAIN?: string
  readonly VITE_BOT_ADMIN_PATH?: string
  readonly VITE_CLOUD_HOST?: string
  readonly VITE_CRYPTO_PATH?: string
  readonly VITE_HUB_NAME?: string
  readonly VITE_MATRIX_HOST?: string
  readonly VITE_METUBE_PATH?: string
  readonly VITE_N8N_PATH?: string
  readonly VITE_PDF_PATH?: string
  readonly VITE_PHOTOS_HOST?: string
  readonly VITE_SENSOR_PATH?: string
  readonly VITE_UPSCALE_PATH?: string
  readonly VITE_XUI_PANEL_PATH?: string
  readonly VITE_XUI_PANEL_PORT?: string
}
