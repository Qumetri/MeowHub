import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './' makes the build path-agnostic, so the secret path
// (DASHBOARD_PATH in ../.env) can change without rebuilding.
export default defineConfig({
  base: './',
  plugins: [react()],
})
