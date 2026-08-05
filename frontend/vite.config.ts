import { defineConfig, type ProxyOptions } from 'vite'
import react from '@vitejs/plugin-react'

const DEFAULT_DEV_API_BASE_URL = 'http://127.0.0.1:8000'

const createDevApiProxy = (): ProxyOptions => {
  const target = process.env.AI_PLATFORM_DEV_API_BASE_URL?.trim() || DEFAULT_DEV_API_BASE_URL
  const apiKey = process.env.AI_PLATFORM_DEV_API_KEY?.trim()

  return {
    target,
    changeOrigin: true,
    headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : undefined,
  }
}

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react()],
  ...(command === 'serve'
    ? {
        server: {
          proxy: {
            '/api': createDevApiProxy(),
            '/v1': createDevApiProxy(),
          },
        },
      }
    : {}),
}))
