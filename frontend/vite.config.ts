import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => ({
  plugins: [react(), tailwindcss()],
  server: { proxy: { '/api': { target: loadEnv(mode, '.', 'SENTINEL_').SENTINEL_DEV_API_URL || 'http://localhost:8000', rewrite: path => path.replace(/^\/api/, '') } } },
}))
