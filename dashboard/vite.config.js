import { defineConfig, createServer } from 'vite'
import react from '@vitejs/plugin-react'
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

// Writes dist/services.json: the same cards the page renders, as data, for
// the helper bot's "links" and health checks. Loaded through Vite's SSR
// loader so import.meta.env resolves exactly as it does in the page — one
// list, no second copy to drift.
function servicesJson() {
  let outDir = 'dist'
  return {
    name: 'services-json',
    apply: 'build',
    configResolved(c) { outDir = c.build.outDir },
    async closeBundle() {
      const server = await createServer({
        configFile: false, logLevel: 'error',
        server: { middlewareMode: true, hmr: false }, appType: 'custom',
      })
      try {
        const { services } = await server.ssrLoadModule('/src/services.js')
        const data = services.map(({ id, name, description, url, status }) =>
          ({ id, name, description, url, status }))
        writeFileSync(resolve(outDir, 'services.json'), JSON.stringify(data, null, 2) + '\n')
      } finally {
        await server.close()
      }
    },
  }
}

// base './' makes the build path-agnostic, so the secret path
// (DASHBOARD_PATH in ../.env) can change without rebuilding.
export default defineConfig({
  base: './',
  plugins: [react(), servicesJson()],
})
