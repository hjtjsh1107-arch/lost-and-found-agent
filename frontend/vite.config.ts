import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig, loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'BACKEND_')
  const target = process.env.BACKEND_URL || env.BACKEND_URL || 'http://127.0.0.1:8000'
  const proxy = {
    '/api': {
      target,
      rewrite: (path: string) => path.replace(/^\/api(?=\/|$)/, ''),
    },
  }
  return {
    plugins: [react(), tailwindcss()],
    server: { host: '127.0.0.1', port: 5173, strictPort: true, proxy },
    preview: { host: '127.0.0.1', port: 4173, strictPort: true, proxy },
  }
})
