import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './' keeps the build path-agnostic: the app is served under a secret
// path prefix (and under /helper-style prefixes on the hub), so every asset
// and API URL must be relative.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: { outDir: 'dist', emptyOutDir: true },
})
