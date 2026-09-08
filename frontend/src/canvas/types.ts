import type { CanvasDAG, CanvasNode, CanvasEdge, CanvasConfig } from './client.ts'

export type { CanvasDAG, CanvasNode, CanvasEdge, CanvasConfig }

export type AgentNodeStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'

export type AgentNodeData = {
  label: string
  role: string
  description: string
  config: CanvasNode['config']
  status?: AgentNodeStatus
}

export type AgentFlowNode = {
  id: string
  type: 'agent'
  position: { x: number; y: number }
  data: AgentNodeData
}

export type AgentFlowEdge = {
  id: string
  source: string
  target: string
}

export const ROLE_LABELS: Record<string, string> = {
  research: 'Research',
  writer: 'Writer',
  reviewer: 'Reviewer',
  custom: 'Custom',
}

export const ROLE_COLORS: Record<string, string> = {
  research: '#3b82f6',
  writer: '#10b981',
  reviewer: '#f59e0b',
  custom: '#8b5cf6',
}
