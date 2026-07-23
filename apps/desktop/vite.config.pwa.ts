import path from 'path'
import fs from 'fs'
import { defineConfig, mergeConfig, type Plugin } from 'vite'

import base from './vite.config'

// The gateway (`hermes serve` + HERMES_WEB_DIST=<dist-pwa>) serves index.html
// for unmatched routes and injects the session token into it. Vite names the
// entry after its HTML input, so rename pwa.html → index.html post-build.
const renamePwaEntryHtml = (): Plugin => ({
  name: 'hermes-pwa-rename-entry',
  closeBundle() {
    const outDir = path.resolve(__dirname, 'dist-pwa')
    const from = path.join(outDir, 'pwa.html')
    const to = path.join(outDir, 'index.html')
    if (fs.existsSync(from)) {
      fs.renameSync(from, to)
    }
  }
})

// Plain-browser PWA build of the desktop renderer (tailnet PWA surface).
// Shares everything with the Electron renderer build except the entry
// (pwa.html → src/pwa/main.tsx, which installs the browser hermesDesktop
// shim) and the output directory.
export default mergeConfig(
  base,
  defineConfig({
    build: {
      outDir: 'dist-pwa',
      emptyOutDir: true,
      rolldownOptions: {
        input: path.resolve(__dirname, 'pwa.html')
      }
    },
    plugins: [renamePwaEntryHtml()]
  })
)
