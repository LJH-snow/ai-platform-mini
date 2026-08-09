import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    css: true,
    environment: 'jsdom',
    testTimeout: 10_000,
    hookTimeout: 10_000,
    include: ['src/**/*.test.{ts,tsx}'],
  },
})
