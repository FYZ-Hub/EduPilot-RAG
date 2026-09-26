import { getJson } from './client'

export type CapabilityState = 'ready' | 'unconfigured' | 'unavailable'

export interface ProviderStatus {
  provider: string
  device: string | null
  ready: boolean
}

export interface HealthResponse {
  status: 'healthy' | 'degraded'
  version: string
  capabilities: {
    documents: CapabilityState
    chat: CapabilityState
    planning: CapabilityState
  }
  providers: {
    embedding: ProviderStatus
    reranker: ProviderStatus
    llm: ProviderStatus
  }
}

export function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return getJson<HealthResponse>('/health', signal)
}
