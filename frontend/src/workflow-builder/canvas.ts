import type { Edge, Node } from '@xyflow/react'

import type {
  CanvasPosition,
  WorkflowBuilderBranch,
  WorkflowBuilderDefinition,
  WorkflowBuilderEdge,
  WorkflowBuilderNode,
  WorkflowBuilderNodeConfig,
  WorkflowBuilderNodeType,
} from './types.ts'

export const WORKFLOW_BUILDER_NODE_LABELS: Record<WorkflowBuilderNodeType, string> = {
  input: '输入',
  llm: 'LLM',
  knowledge: '知识库',
  tool: '工具',
  condition: '条件',
  agent: 'Agent',
  output: '输出',
}

export type BuilderCanvasNode = Node<{ node: WorkflowBuilderNode }>
export type BuilderCanvasEdge = Edge

const DEFAULT_COLUMN_GAP = 260
const DEFAULT_ROW_GAP = 140

const isValidPosition = (value: unknown): value is CanvasPosition => {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as { x?: unknown; y?: unknown }
  return (
    typeof candidate.x === 'number' &&
    Number.isFinite(candidate.x) &&
    typeof candidate.y === 'number' &&
    Number.isFinite(candidate.y)
  )
}

const defaultPosition = (index: number): CanvasPosition => ({
  x: (index % 4) * DEFAULT_COLUMN_GAP,
  y: Math.floor(index / 4) * DEFAULT_ROW_GAP,
})

export const createBuilderNode = (
  type: WorkflowBuilderNodeType,
  id: string,
  position: CanvasPosition,
): WorkflowBuilderNode => {
  const base: WorkflowBuilderNodeConfig = { canvas_position: position }
  const configByType: Partial<Record<WorkflowBuilderNodeType, WorkflowBuilderNodeConfig>> = {
    input: {},
    llm: { prompt_template: '' },
    knowledge: { query_template: '' },
    tool: { tool: 'calculator', arguments_template: { expression: '{{input.text}}' } },
    condition: { branches: [{ id: 'branch-1', condition: null, target: '' }] },
    agent: { agent_id: '', prompt: '' },
    output: { output_template: '{{input.text}}' },
  }
  return {
    id,
    type,
    config: { ...base, ...configByType[type] },
  }
}

export const createEmptyDefinition = (): WorkflowBuilderDefinition => {
  const input = createBuilderNode('input', 'input-1', { x: 0, y: 0 })
  const output = createBuilderNode('output', 'output-1', { x: DEFAULT_COLUMN_GAP, y: 0 })
  return {
    nodes: [input, output],
    edges: [{ from: input.id, to: output.id }],
    version: 1,
  }
}

export const definitionToCanvas = (
  definition: WorkflowBuilderDefinition,
): { nodes: BuilderCanvasNode[]; edges: BuilderCanvasEdge[] } => {
  const canvasNodes = definition.nodes.map((node, index): BuilderCanvasNode => {
    const storedPosition = node.config.canvas_position
    const position = isValidPosition(storedPosition) ? storedPosition : defaultPosition(index)
    return {
      id: node.id,
      type: node.type,
      position,
      data: { node },
    }
  })
  const canvasEdges = definition.edges.map(
    (edge): BuilderCanvasEdge => ({
      id: `${edge.from}->${edge.to}`,
      source: edge.from,
      target: edge.to,
    }),
  )
  return { nodes: canvasNodes, edges: canvasEdges }
}

export const canvasToDefinition = (
  nodes: BuilderCanvasNode[],
  edges: BuilderCanvasEdge[],
): WorkflowBuilderDefinition => {
  const definitionNodes = nodes.map((node): WorkflowBuilderNode => {
    const storedConfig = node.data?.node?.config ?? {}
    return {
      id: node.id,
      type: node.type as WorkflowBuilderNodeType,
      config: {
        ...storedConfig,
        canvas_position: {
          x: Math.round(node.position.x),
          y: Math.round(node.position.y),
        },
      },
    }
  })
  const definitionEdges: WorkflowBuilderEdge[] = edges.flatMap((edge) => {
    if (!edge.source || !edge.target) return []
    return [{ from: edge.source, to: edge.target }]
  })
  return {
    nodes: definitionNodes,
    edges: definitionEdges,
    version: 1,
  }
}

export const nextNodeId = (nodes: BuilderCanvasNode[]): string => {
  const highest = nodes.reduce((current, node) => {
    const match = /^node-(\d+)$/.exec(node.id)
    if (!match) return current
    return Math.max(current, Number(match[1]))
  }, 0)
  return `node-${highest + 1}`
}

export const nextNodePosition = (nodes: BuilderCanvasNode[]): CanvasPosition => {
  if (nodes.length === 0) return { x: 0, y: 0 }
  const maxX = Math.max(0, ...nodes.map((node) => node.position.x))
  const maxY = Math.max(0, ...nodes.map((node) => node.position.y))
  return { x: maxX + DEFAULT_COLUMN_GAP, y: maxY }
}

export const edgeId = (source: string, target: string): string => `${source}->${target}`

export const canConnect = (
  source: WorkflowBuilderNode | undefined,
  target: WorkflowBuilderNode | undefined,
  nodes: WorkflowBuilderNode[],
  edges: WorkflowBuilderEdge[],
): string | null => {
  if (!source || !target) return '请选择两个有效节点。'
  if (source.id === target.id) return '节点不能连接到自身。'
  if (source.type === 'condition') {
    return '条件节点的分支请通过节点配置中的 branches 设置，不使用普通连线。'
  }
  if (edges.some((edge) => edge.to === target.id)) {
    return `节点 ${target.id} 已有一条入边，当前串行流只允许一条入边。`
  }
  if (source.type === 'input' && edges.some((edge) => edge.from === source.id)) {
    return 'input 节点只允许一条出边。'
  }
  const branchTargets = nodes
    .filter((node) => node.type === 'condition')
    .flatMap((node) => (node.config.branches ?? []).map((branch) => branch.target))
  if (branchTargets.includes(target.id)) {
    return `节点 ${target.id} 已作为条件分支目标，不能再接收普通入边。`
  }
  return null
}

export const validateDefinitionForSave = (
  name: string,
  definition: WorkflowBuilderDefinition,
): string | null => {
  const errors: string[] = []
  if (!name.trim()) {
    errors.push('流程名称不能为空。')
  }
  const seenIds = new Set<string>()
  for (const node of definition.nodes) {
    if (seenIds.has(node.id)) {
      errors.push(`节点 id 重复：${node.id}`)
    }
    seenIds.add(node.id)
  }
  const byId = new Map(definition.nodes.map((node) => [node.id, node]))
  const incomingCounts = new Map<string, number>()
  const countIncoming = (targetId: string): void => {
    incomingCounts.set(targetId, (incomingCounts.get(targetId) ?? 0) + 1)
  }
  if (definition.nodes.length === 0) {
    errors.push('至少需要一个节点。')
  }
  if (!definition.nodes.some((node) => node.type === 'input')) {
    errors.push('定义中缺少 input 节点。')
  }
  if (!definition.nodes.some((node) => node.type === 'output')) {
    errors.push('定义中缺少 output 节点。')
  }

  for (const edge of definition.edges) {
    if (!byId.has(edge.from)) {
      errors.push(`连线起点 ${edge.from} 不存在。`)
    }
    if (!byId.has(edge.to)) {
      errors.push(`连线终点 ${edge.to} 不存在。`)
    }
    if (byId.has(edge.to)) countIncoming(edge.to)
  }

  const inputNodes = definition.nodes.filter((node) => node.type === 'input')
  for (const inputNode of inputNodes) {
    const outgoing = definition.edges.filter((edge) => edge.from === inputNode.id).length
    if (outgoing !== 1) {
      errors.push(`input 节点 ${inputNode.id} 的出边数量必须为 1。`)
    }
  }

  for (const node of definition.nodes) {
    if (node.type === 'llm' && !String(node.config.prompt_template ?? '').trim()) {
      errors.push(`LLM 节点 ${node.id} 缺少 prompt_template。`)
    }
    if (node.type === 'knowledge' && !String(node.config.query_template ?? '').trim()) {
      errors.push(`知识库节点 ${node.id} 缺少 query_template。`)
    }
    if (node.type === 'tool' && !String(node.config.tool ?? '').trim()) {
      errors.push(`工具节点 ${node.id} 缺少 tool 名称。`)
    }
    if (node.type === 'agent' && !String(node.config.agent_id ?? '').trim()) {
      errors.push(`Agent 节点 ${node.id} 缺少 agent_id。`)
    }
    if (node.type === 'condition') {
      const branches = Array.isArray(node.config.branches)
        ? (node.config.branches as WorkflowBuilderBranch[])
        : []
      if (branches.length === 0) {
        errors.push(`条件节点 ${node.id} 至少需要一个分支。`)
      } else {
        const branchTargets = new Set<string>()
        let defaultBranchSeen = false
        for (const branch of branches) {
          if (!branch.target) {
            errors.push(`条件节点 ${node.id} 的分支缺少 target。`)
          } else if (!byId.has(branch.target)) {
            errors.push(`条件节点 ${node.id} 的分支 target ${branch.target} 不存在。`)
          } else if (branchTargets.has(branch.target)) {
            errors.push(`条件节点 ${node.id} 的分支 target ${branch.target} 重复。`)
          } else {
            branchTargets.add(branch.target)
          }
          if (branch.condition === null || branch.condition === '') {
            if (defaultBranchSeen) {
              errors.push(`条件节点 ${node.id} 只允许一个默认分支。`)
            }
            defaultBranchSeen = true
          } else if (!isValidConditionExpression(branch.condition)) {
            errors.push(
              `条件节点 ${node.id} 的分支表达式不合法：${branch.condition}。` +
                "仅支持 {{var}} contains 'x'、{{var}} is empty、{{var}} == 'x'。",
            )
          }
        }
        for (const branch of branches) {
          if (branch.target && byId.has(branch.target)) countIncoming(branch.target)
        }
      }
    }
    if (node.type === 'output' && !String(node.config.output_template ?? '').trim()) {
      errors.push(`输出节点 ${node.id} 缺少 output_template。`)
    }
  }

  for (const node of definition.nodes) {
    if ((incomingCounts.get(node.id) ?? 0) > 1) {
      errors.push(`节点 ${node.id} 的入边数量超过 1，串行流只允许一条入边。`)
    }
  }

  const adjacency = new Map<string, string[]>(definition.nodes.map((node) => [node.id, []]))
  for (const edge of definition.edges) {
    if (byId.has(edge.from) && byId.has(edge.to)) {
      adjacency.get(edge.from)?.push(edge.to)
    }
  }
  for (const node of definition.nodes) {
    if (node.type !== 'condition') continue
    const branches = Array.isArray(node.config.branches)
      ? (node.config.branches as WorkflowBuilderBranch[])
      : []
    for (const branch of branches) {
      if (branch.target && byId.has(branch.target)) {
        adjacency.get(node.id)?.push(branch.target)
      }
    }
  }
  const inDegree = new Map<string, number>(definition.nodes.map((node) => [node.id, 0]))
  for (const targets of adjacency.values()) {
    for (const target of targets) {
      inDegree.set(target, (inDegree.get(target) ?? 0) + 1)
    }
  }
  const ready = definition.nodes
    .filter((node) => (inDegree.get(node.id) ?? 0) === 0)
    .map((node) => node.id)
  const visited = new Set<string>()
  while (ready.length > 0) {
    const current = ready.shift()
    if (!current || visited.has(current)) continue
    visited.add(current)
    for (const target of adjacency.get(current) ?? []) {
      const nextDegree = (inDegree.get(target) ?? 0) - 1
      inDegree.set(target, nextDegree)
      if (nextDegree === 0) ready.push(target)
    }
  }
  if (visited.size !== definition.nodes.length) {
    errors.push('定义存在环，不是 DAG。')
  }

  return errors.length > 0 ? errors.join(' ') : null
}

const isValidConditionExpression = (expression: string): boolean =>
  /^\{\{\s*[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_]+)?\s*\}\}\s+contains\s+'[^']*'$/.test(expression) ||
  /^\{\{\s*[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_]+)?\s*\}\}\s+is\s+empty$/.test(expression) ||
  /^\{\{\s*[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_]+)?\s*\}\}\s*==\s*'[^']*'$/.test(expression)
