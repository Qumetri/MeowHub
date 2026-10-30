import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './': the page lives under a secret path that can change without a rebuild.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: { chunkSizeWarningLimit: 1500 },
  server: {
    // dev: proxy the API to the engine through caddy's network is not reachable from the host,
    // so run `npm run build` and test through the real route instead.
    host: '127.0.0.1',
  },
})
