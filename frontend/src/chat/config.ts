export type RuntimeConfig = {
  apiBaseUrl?: string
  apiKey?: string
  ragEnabled?: boolean
  ragMaxUploadBytes?: number
}

declare global {
  interface Window {
    __AI_PLATFORM_RUNTIME_CONFIG__?: RuntimeConfig
  }
}

export function getRuntimeConfig(): RuntimeConfig {
  if (typeof window === 'undefined') {
    return {}
  }

  return window.__AI_PLATFORM_RUNTIME_CONFIG__ ?? {}
}
