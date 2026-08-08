import { defineConfig } from '@playwright/test'

const REPO_ROOT = '..'
const E2E_DATABASE_URL =
  process.env.E2E_DATABASE_URL ??
  'postgresql+asyncpg://postgres:postgres@localhost:5432/aiplatform'

export default defineConfig({
  testDir: './e2e',
  timeout: 90_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: 'http://127.0.0.1:5174',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'uvicorn app.main:app --host 127.0.0.1 --port 8010',
      cwd: REPO_ROOT,
      url: 'http://127.0.0.1:8010/api/v1/health',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        LLM_PROVIDER: 'mock',
        RAG_ENABLED: 'true',
        AUTH_STORAGE: 'memory',
        CONVERSATION_STORAGE: 'memory',
        WORKFLOW_STORAGE: 'memory',
        DATABASE_URL: E2E_DATABASE_URL,
        RAG_SEARCH_MODE: 'vector',
        INITIAL_API_KEY: '',
        ADMIN_API_KEYS: '',
        RATE_LIMIT_ENABLED: 'false',
        TELEMETRY_ENABLED: 'false',
        METRICS_ENABLED: 'false',
        LOG_LEVEL: 'WARNING',
      },
    },
    {
      command: 'npm run dev -- --port 5174 --strictPort',
      cwd: '.',
      url: 'http://127.0.0.1:5174',
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        AI_PLATFORM_DEV_API_BASE_URL: 'http://127.0.0.1:8010',
      },
    },
  ],
})
