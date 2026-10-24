import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The FastAPI backend runs on :8000 (`uv run uvicorn app.api.main:app --reload`).
// Everything under /api is proxied there, so the browser never needs CORS and phones on the LAN work too.
// `vite preview` behind a Cloudflare quick tunnel (cloudflared tunnel --url http://localhost:4173) serves the
// built app on a public *.trycloudflare.com address with the same /api proxy.
const proxy = {
  '/api': {
    target: process.env.API_URL ?? 'http://127.0.0.1:8000',
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ''),
  },
}
const allowedHosts = ['.trycloudflare.com']

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { host: true, proxy, allowedHosts },
  preview: { host: true, proxy, allowedHosts },
})
