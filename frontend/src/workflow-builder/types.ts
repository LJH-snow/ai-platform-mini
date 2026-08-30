export const WORKFLOW_BUILDER_NODE_TYPES = [
  'input',
  'llm',
  'knowledge',
  'tool',
  'condition',
  'agent',
  'output',
] as const

export type WorkflowBuilderNodeType = (typeof WORKFLOW_BUILDER_NODE_TYPES)[number]

export type CanvasPosition = {
  x: number
  y: number
}

export type WorkflowBuilderBranch = {
  id: string
  condition: string | null
  target: string
}

export type WorkflowBuilderNodeConfig = {
  canvas_position?: CanvasPosition
  model?: string
  system_prompt?: string
  prompt_template?: string
  query_template?: string
  top_k?: number
  tool?: string
  arguments_template?: unknown
  branches?: WorkflowBuilderBranch[]
  agent_id?: string
  prompt?: string
  output_template?: string
  [key: string]: unknown
}

export type WorkflowBuilderNode = {
  id: string
  type: WorkflowBuilderNodeType
  config: WorkflowBuilderNodeConfig
}

export type WorkflowBuilderEdge = {
  from: string
  to: string
}

export type WorkflowBuilderDefinition = {
  nodes: WorkflowBuilderNode[]
  edges: WorkflowBuilderEdge[]
  version: number
}

export type WorkflowBuilderWorkflow = {
  id: string
  workspace_id: string
  name: string
  description: string
  status: 'draft' | 'published'
  version: number
  definition: WorkflowBuilderDefinition
  created_by: string | null
  created_at: string | null
  updated_at: string | null
}

export type WorkflowBuilderWorkflowInput = {
  name: string
  description?: string
  definition: WorkflowBuilderDefinition
}

export type WorkflowBuilderUpdateInput = Partial<WorkflowBuilderWorkflowInput>

export type WorkflowBuilderNodeResult = {
  node_id: string
  type: WorkflowBuilderNodeType
  status: 'completed' | 'failed'
  started_at: string
  duration_ms: number
  input_summary: string | null
  output_summary: string | null
  error: string | null
}

export type WorkflowBuilderRun = {
  id: string
  workflow_id: string
  workspace_id: string
  status: 'running' | 'completed' | 'failed' | 'cancelled'
  inputs: Record<string, unknown>
  definition: WorkflowBuilderDefinition
  node_results: WorkflowBuilderNodeResult[]
  error: string | null
  total_duration_ms: number | null
  created_at: string | null
  completed_at: string | null
}
