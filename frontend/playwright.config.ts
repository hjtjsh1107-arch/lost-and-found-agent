import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { randomUUID } from 'node:crypto'

const root = fileURLToPath(new URL('..', import.meta.url))
const frontend = fileURLToPath(new URL('.', import.meta.url))
const python = process.env.LAF_PYTHON || resolve(root, '.venv', 'Scripts', 'python.exe')
const backend = 'http://127.0.0.1:18021'
const web = 'http://127.0.0.1:15173'

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 60_000,
  use: {
    baseURL: web,
    channel: process.env.LAF_BROWSER_CHANNEL || undefined,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: `"${python}" -m uvicorn backend.main:app --host 127.0.0.1 --port 18021 --no-access-log`,
      cwd: root,
      url: `${backend}/health`,
      timeout: 60_000,
      env: {
        APP_MODE: 'mock',
        STAFF_TOKEN: 'local-mock-only',
        PYTHONUTF8: '1',
        DATABASE_URL: process.env.LAF_E2E_POSTGRES_URL || `sqlite:///./instance/e2e-${randomUUID()}.db`,
        MAX_REEXTRACTIONS: '2',
        MAX_QUESTIONS: '2',
      },
    },
    {
      command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 15173 --strictPort',
      cwd: frontend,
      url: web,
      env: { BACKEND_URL: backend },
      timeout: 60_000,
    },
  ],
})
