import type { ChangeEvent, DragEvent, JSX } from 'react'
import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  ReactFlowProvider,
  addEdge,
  applyEdgeChanges,
  applyNodeChanges,
  useReactFlow,
  type Connection,
  type IsValidConnection,
  type NodeChange,
  type NodeMouseHandler,
  type NodeProps,
  type NodeTypes,
  type OnEdgesChange,
  type OnNodesChange,
  type OnNodesDelete,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import './WorkflowBuilder.css'
import type { AgentSummary, ConfigClient, ToolInfo } from '../platform/config-client.ts'
import {
  WORKFLOW_BUILDER_NODE_LABELS,
  canConnect,
  canvasToDefinition,
  createBuilderNode,
  createEmptyDefinition,
  definitionToCanvas,
  edgeId,
  nextNodeId,
  nextNodePosition,
  validateDefinitionForSave,
  type BuilderCanvasEdge,
  type BuilderCanvasNode,
} from './canvas.ts'
import {
  WorkflowBuilderApiError,
  WorkflowBuilderNetworkError,
  type WorkflowBuilderClient,
} from './client.ts'
import {
  WORKFLOW_BUILDER_NODE_TYPES,
  type CanvasPosition,
  type WorkflowBuilderBranch,
  type WorkflowBuilderDefinition,
  type WorkflowBuilderNodeConfig,
  type WorkflowBuilderNodeType,
  type WorkflowBuilderRun,
  type WorkflowBuilderWorkflow,
} from './types.ts'

type WorkflowBuilderProps = {
  apiKeyConfigured: boolean
  client: WorkflowBuilderClient
  configClient: ConfigClient
}

type WorkflowCanvasProps = {
  nodes: BuilderCanvasNode[]
  edges: BuilderCanvasEdge[]
  onNodesChange: OnNodesChange<BuilderCanvasNode>
  onEdgesChange: OnEdgesChange<BuilderCanvasEdge>
  onConnect: (connection: Connection) => void
  isValidConnection: IsValidConnection<BuilderCanvasEdge>
  onNodeClick: NodeMouseHandler<BuilderCanvasNode>
  onPaneClick: () => void
  onNodesDelete: OnNodesDelete<BuilderCanvasNode>
  onEdgesDelete: (deleted: BuilderCanvasEdge[]) => void
  addNodeAt: (type: WorkflowBuilderNodeType, position: CanvasPosition) => void
}

const NODE_DRAG_MIME = 'application/x-workflow-builder-node'

const isBuilderNodeType = (value: string): value is WorkflowBuilderNodeType =>
  (WORKFLOW_BUILDER_NODE_TYPES as readonly string[]).includes(value)

const workflowBuilderErrorMessage = (error: unknown): string => {
  if (error instanceof WorkflowBuilderApiError) return error.message
  if (error instanceof WorkflowBuilderNetworkError) return error.message
  return '请求失败，请稍后重试。'
}

const formatTimestamp = (value: string | null): string => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

const stringifyJson = (value: unknown): string =>
  JSON.stringify(value ?? { expression: '{{input.text}}' }, null, 2)

function BuilderNode({ id, data, selected }: NodeProps<BuilderCanvasNode>): JSX.Element {
  const node = data.node
  const branchCount = node.type === 'condition' ? (node.config.branches ?? []).length : 0
  const className = [
    'builderFlowNode',
    `builderFlowNode-${node.type}`,
    selected ? 'builderFlowNodeSelected' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={className}>
      {node.type !== 'input' && node.type !== 'condition' ? (
        <Handle type="target" position={Position.Left} className="builderFlowHandle" />
      ) : null}
      <span className="builderFlowNodeKind">{WORKFLOW_BUILDER_NODE_LABELS[node.type]}</span>
      <span className="builderFlowNodeId">{id}</span>
      {node.type === 'condition' && branchCount > 0 ? (
        <span className="builderFlowNodeMeta">{branchCount} 分支</span>
      ) : null}
      {node.type !== 'output' && node.type !== 'condition' ? (
        <Handle type="source" position={Position.Right} className="builderFlowHandle" />
      ) : null}
    </div>
  )
}

const builderNodeTypes: NodeTypes = {
  input: BuilderNode,
  llm: BuilderNode,
  knowledge: BuilderNode,
  tool: BuilderNode,
  condition: BuilderNode,
  agent: BuilderNode,
  output: BuilderNode,
}

function WorkflowCanvas(props: WorkflowCanvasProps): JSX.Element {
  const reactFlow = useReactFlow()

  const handleDragOver = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault()
    event.dataTransfer.dropEffect = 'move'
  }

  const handleDrop = (event: DragEvent<HTMLDivElement>): void => {
    event.preventDefault()
    const nodeType = event.dataTransfer.getData(NODE_DRAG_MIME)
    if (!isBuilderNodeType(nodeType)) return
    const position = reactFlow.screenToFlowPosition({
      x: event.clientX,
      y: event.clientY,
    })
    props.addNodeAt(nodeType, position)
  }

  return (
    <div className="builderCanvas" onDragOver={handleDragOver} onDrop={handleDrop}>
      <ReactFlow
        nodes={props.nodes}
        edges={props.edges}
        nodeTypes={builderNodeTypes}
        onNodesChange={props.onNodesChange}
        onEdgesChange={props.onEdgesChange}
        onConnect={props.onConnect}
        isValidConnection={props.isValidConnection}
        onNodeClick={props.onNodeClick}
        onPaneClick={props.onPaneClick}
        onNodesDelete={props.onNodesDelete}
        onEdgesDelete={props.onEdgesDelete}
        fitView
      >
        <Background />
        <Controls />
        <MiniMap />
      </ReactFlow>
    </div>
  )
}

export function WorkflowBuilder({
  apiKeyConfigured,
  client,
  configClient,
}: WorkflowBuilderProps): JSX.Element {
  const [workflows, setWorkflows] = useState<WorkflowBuilderWorkflow[]>([])
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [nodes, setNodes] = useState<BuilderCanvasNode[]>([])
  const [edges, setEdges] = useState<BuilderCanvasEdge[]>([])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [tools, setTools] = useState<ToolInfo[]>([])
  const [agents, setAgents] = useState<AgentSummary[]>([])
  const [runs, setRuns] = useState<WorkflowBuilderRun[]>([])
  const [selectedRun, setSelectedRun] = useState<WorkflowBuilderRun | null>(null)
  const [runInput, setRunInput] = useState('{"text": "你好，请分析一下"}')
  const [toolArgumentsText, setToolArgumentsText] = useState(
    JSON.stringify({ expression: '{{input.text}}' }, null, 2),
  )
  const [toolArgumentsError, setToolArgumentsError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [runLoading, setRunLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const loadSeqRef = useRef(0)

  const selectedWorkflow = workflows.find((workflow) => workflow.id === selectedWorkflowId) ?? null
  const selectedCanvasNode = nodes.find((canvasNode) => canvasNode.id === selectedNodeId) ?? null
  const selectedDefinitionNode = selectedCanvasNode?.data.node ?? null

  const refreshWorkflows = useCallback(async (): Promise<void> => {
    setLoading(true)
    setError(null)
    try {
      const items = await client.listWorkflows()
      setWorkflows(items)
    } catch (caught) {
      setError(workflowBuilderErrorMessage(caught))
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    if (!apiKeyConfigured) {
      setLoading(false)
      setWorkflows([])
      return
    }
    void refreshWorkflows()
  }, [apiKeyConfigured, refreshWorkflows])

  useEffect(() => {
    if (!apiKeyConfigured) return

    const loadOptions = async (): Promise<void> => {
      const [toolItems, agentItems] = await Promise.all([
        configClient.listTools().catch(() => [] as ToolInfo[]),
        configClient.listAgents().catch(() => [] as AgentSummary[]),
      ])
      setTools(toolItems)
      setAgents(agentItems)
    }
    void loadOptions()
  }, [apiKeyConfigured, configClient])

  const openWorkflow = useCallback(
    async (workflow: WorkflowBuilderWorkflow): Promise<void> => {
      const seq = ++loadSeqRef.current
      setSelectedWorkflowId(workflow.id)
      setName(workflow.name)
      setDescription(workflow.description)
      const canvas = definitionToCanvas(workflow.definition)
      setNodes(canvas.nodes)
      setEdges(canvas.edges)
      setSelectedNodeId(null)
      setSelectedRun(null)
      setRuns([])
      setError(null)
      setNotice(null)
      try {
        const history = await client.listRuns(workflow.id)
        if (loadSeqRef.current === seq) setRuns(history)
      } catch {
        if (loadSeqRef.current === seq) setRuns([])
      }
    },
    [client],
  )

  const createNewWorkflow = (): void => {
    loadSeqRef.current += 1
    const canvas = definitionToCanvas(createEmptyDefinition())
    setSelectedWorkflowId(null)
    setName('')
    setDescription('')
    setNodes(canvas.nodes)
    setEdges(canvas.edges)
    setSelectedNodeId(null)
    setSelectedRun(null)
    setRuns([])
    setError(null)
    setNotice('已新建草稿，保存后写入服务端。')
  }

  const selectNode = useCallback(
    (nodeId: string | null): void => {
      setSelectedNodeId(nodeId)
      const canvasNode = nodes.find((candidate) => candidate.id === nodeId)
      if (canvasNode?.data.node.type === 'tool') {
        setToolArgumentsText(stringifyJson(canvasNode.data.node.config.arguments_template))
        setToolArgumentsError(null)
      }
    },
    [nodes],
  )

  const addNodeAt = useCallback(
    (type: WorkflowBuilderNodeType, position: CanvasPosition): void => {
      const id = nextNodeId(nodes)
      const definitionNode = createBuilderNode(type, id, position)
      const canvasNode: BuilderCanvasNode = {
        id,
        type,
        position,
        data: { node: definitionNode },
      }
      setNodes((current) => [...current, canvasNode])
      setSelectedNodeId(id)
      setNotice(`已添加 ${WORKFLOW_BUILDER_NODE_LABELS[type]} 节点。`)
    },
    [nodes],
  )

  const removeNode = useCallback((nodeId: string): void => {
    setNodes((current) =>
      current
        .filter((canvasNode) => canvasNode.id !== nodeId)
        .map((canvasNode) => {
          if (canvasNode.data.node.type !== 'condition') return canvasNode
          const branches = (canvasNode.data.node.config.branches ?? []).filter(
            (branch) => branch.target !== nodeId,
          )
          return {
            ...canvasNode,
            data: {
              ...canvasNode.data,
              node: {
                ...canvasNode.data.node,
                config: { ...canvasNode.data.node.config, branches },
              },
            },
          }
        }),
    )
    setEdges((current) =>
      current.filter((edge) => edge.source !== nodeId && edge.target !== nodeId),
    )
    setSelectedNodeId((current) => (current === nodeId ? null : current))
  }, [])

  const handleNodesChange: OnNodesChange<BuilderCanvasNode> = useCallback(
    (changes: NodeChange<BuilderCanvasNode>[]) => {
      setNodes((current) => applyNodeChanges(changes, current))
    },
    [],
  )

  const handleEdgesChange: OnEdgesChange<BuilderCanvasEdge> = useCallback((changes) => {
    setEdges((current) => applyEdgeChanges(changes, current))
  }, [])

  const handleConnect = useCallback((connection: Connection): void => {
    if (!connection.source || !connection.target) return
    setEdges((current) =>
      addEdge({ ...connection, id: edgeId(connection.source, connection.target) }, current),
    )
  }, [])

  const handleConnectionCheck: IsValidConnection<BuilderCanvasEdge> = useCallback(
    (connection) => {
      const sourceNode = nodes.find((candidate) => candidate.id === connection.source)
      const targetNode = nodes.find((candidate) => candidate.id === connection.target)
      const definitionEdges = edges.flatMap((edge) =>
        edge.source && edge.target ? [{ from: edge.source, to: edge.target }] : [],
      )
      const message = canConnect(
        sourceNode?.data.node,
        targetNode?.data.node,
        nodes.map((candidate) => candidate.data.node),
        definitionEdges,
      )
      if (message) {
        setError(message)
        return false
      }
      setError(null)
      return true
    },
    [edges, nodes],
  )

  const handleNodesDelete: OnNodesDelete<BuilderCanvasNode> = useCallback(
    (deleted) => {
      for (const node of deleted) removeNode(node.id)
    },
    [removeNode],
  )

  const handleEdgesDelete = useCallback((deleted: BuilderCanvasEdge[]): void => {
    const deletedIds = new Set(deleted.map((edge) => edge.id))
    setEdges((current) => current.filter((edge) => !deletedIds.has(edge.id)))
  }, [])

  const updateSelectedConfig = useCallback(
    (patch: Partial<WorkflowBuilderNodeConfig>): void => {
      setNodes((current) =>
        current.map((canvasNode) => {
          if (canvasNode.id !== selectedNodeId) return canvasNode
          return {
            ...canvasNode,
            data: {
              ...canvasNode.data,
              node: {
                ...canvasNode.data.node,
                config: {
                  ...canvasNode.data.node.config,
                  ...patch,
                },
              },
            },
          }
        }),
      )
    },
    [selectedNodeId],
  )

  const updateBranch = (branchId: string, patch: Partial<WorkflowBuilderBranch>): void => {
    if (selectedDefinitionNode?.type !== 'condition') return
    const branches = (selectedDefinitionNode.config.branches ?? []).map((branch) =>
      branch.id === branchId ? { ...branch, ...patch } : branch,
    )
    updateSelectedConfig({ branches })
  }

  const addBranch = (): void => {
    if (selectedDefinitionNode?.type !== 'condition') return
    const branchId = `branch-${crypto.randomUUID()}`
    const firstTarget = nodes.find((node) => node.id !== selectedNodeId)?.id ?? ''
    const branches = [
      ...(selectedDefinitionNode.config.branches ?? []),
      { id: branchId, condition: null, target: firstTarget },
    ]
    updateSelectedConfig({ branches })
  }

  const removeBranch = (branchId: string): void => {
    if (selectedDefinitionNode?.type !== 'condition') return
    const branches = (selectedDefinitionNode.config.branches ?? []).filter(
      (branch) => branch.id !== branchId,
    )
    updateSelectedConfig({ branches })
  }

  const updateToolArguments = (event: ChangeEvent<HTMLTextAreaElement>): void => {
    const value = event.target.value
    setToolArgumentsText(value)
    try {
      const parsed: unknown = value.trim() ? JSON.parse(value) : {}
      updateSelectedConfig({ arguments_template: parsed })
      setToolArgumentsError(null)
    } catch {
      setToolArgumentsError('arguments_template 必须是合法 JSON。')
    }
  }

  const definitionForMutation = (): WorkflowBuilderDefinition | null => {
    if (toolArgumentsError) {
      setError(toolArgumentsError)
      return null
    }
    const definition = canvasToDefinition(nodes, edges)
    const validationMessage = validateDefinitionForSave(name, definition)
    if (validationMessage) {
      setError(validationMessage)
      return null
    }
    return definition
  }

  const saveDraftBeforeAction = async (
    definition: WorkflowBuilderDefinition,
  ): Promise<WorkflowBuilderWorkflow | null> => {
    if (selectedWorkflow === null || selectedWorkflow.status === 'published') {
      return selectedWorkflow
    }
    const updated = await client.updateWorkflow(selectedWorkflow.id, {
      name: name.trim(),
      description: description.trim(),
      definition,
    })
    setWorkflows((current) =>
      current.map((workflow) => (workflow.id === updated.id ? updated : workflow)),
    )
    return updated
  }

  const saveWorkflow = async (): Promise<void> => {
    if (selectedWorkflow?.status === 'published') {
      setError('已发布流程禁止直接修改，请先取消发布。')
      return
    }
    const definition = definitionForMutation()
    if (!definition) return

    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      if (selectedWorkflowId === null) {
        const created = await client.createWorkflow({
          name: name.trim(),
          description: description.trim(),
          definition,
        })
        setWorkflows((current) => [
          created,
          ...current.filter((workflow) => workflow.id !== created.id),
        ])
        setSelectedWorkflowId(created.id)
        setNotice('流程已创建并保存。')
      } else {
        const updated = await client.updateWorkflow(selectedWorkflowId, {
          name: name.trim(),
          description: description.trim(),
          definition,
        })
        setWorkflows((current) =>
          current.map((workflow) => (workflow.id === updated.id ? updated : workflow)),
        )
        setNotice('草稿已保存。')
      }
    } catch (caught) {
      setError(workflowBuilderErrorMessage(caught))
    } finally {
      setSaving(false)
    }
  }

  const publishWorkflow = async (): Promise<void> => {
    if (selectedWorkflowId === null) return
    if (selectedWorkflow?.status === 'published') {
      setNotice('该流程已经发布。')
      return
    }
    const definition = definitionForMutation()
    if (!definition) return

    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const updated = await saveDraftBeforeAction(definition)
      if (updated === null) return
      const published = await client.publishWorkflow(updated.id)
      setWorkflows((current) =>
        current.map((workflow) => (workflow.id === published.id ? published : workflow)),
      )
      setNotice(`流程已发布，当前版本 v${published.version}。`)
    } catch (caught) {
      setError(workflowBuilderErrorMessage(caught))
    } finally {
      setSaving(false)
    }
  }

  const unpublishWorkflow = async (): Promise<void> => {
    if (selectedWorkflowId === null || selectedWorkflow?.status !== 'published') {
      setNotice('当前流程无需取消发布。')
      return
    }
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const updated = await client.unpublishWorkflow(selectedWorkflowId)
      setWorkflows((current) =>
        current.map((workflow) => (workflow.id === updated.id ? updated : workflow)),
      )
      setNotice('流程已取消发布，可以继续编辑。')
    } catch (caught) {
      setError(workflowBuilderErrorMessage(caught))
    } finally {
      setSaving(false)
    }
  }

  const deleteWorkflow = async (): Promise<void> => {
    if (selectedWorkflow === null) return
    if (selectedWorkflow.status === 'published') {
      setError('已发布流程不能直接删除，请先取消发布。')
      return
    }
    if (!window.confirm(`确定删除流程「${selectedWorkflow.name}」？`)) return

    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      await client.deleteWorkflow(selectedWorkflow.id)
      setWorkflows((current) => current.filter((workflow) => workflow.id !== selectedWorkflow.id))
      setSelectedWorkflowId(null)
      setName('')
      setDescription('')
      setNodes([])
      setEdges([])
      setSelectedNodeId(null)
      setRuns([])
      setSelectedRun(null)
      setNotice('流程已删除。')
    } catch (caught) {
      setError(workflowBuilderErrorMessage(caught))
    } finally {
      setSaving(false)
    }
  }

  const loadRuns = useCallback(
    async (workflowId: string): Promise<void> => {
      try {
        const history = await client.listRuns(workflowId)
        setRuns(history)
      } catch {
        setRuns([])
      }
    },
    [client],
  )

  const runWorkflow = async (): Promise<void> => {
    if (selectedWorkflow === null) {
      setError('请先保存草稿，再进行试运行。')
      return
    }
    let inputs: Record<string, unknown>
    try {
      const parsed: unknown = JSON.parse(runInput)
      if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
        throw new Error('input object')
      }
      inputs = parsed as Record<string, unknown>
    } catch {
      setError('试运行输入必须是 JSON 对象。')
      return
    }

    setRunLoading(true)
    setError(null)
    setNotice(null)
    try {
      if (selectedWorkflow.status !== 'published') {
        const definition = definitionForMutation()
        if (definition === null) return
        const saved = await saveDraftBeforeAction(definition)
        if (saved === null) return
      }
      const run = await client.runWorkflow(selectedWorkflow.id, inputs)
      setSelectedRun(run)
      await loadRuns(selectedWorkflow.id)
      setNotice('试运行已完成。')
    } catch (caught) {
      setError(workflowBuilderErrorMessage(caught))
    } finally {
      setRunLoading(false)
    }
  }

  const branchTargetOptions = nodes.filter((node) => node.id !== selectedNodeId)
  const enabledTools = tools.filter((tool) => tool.enabled)
  const configTool =
    selectedDefinitionNode?.type === 'tool' &&
    typeof selectedDefinitionNode.config.tool === 'string'
      ? selectedDefinitionNode.config.tool
      : ''
  const configAgent =
    selectedDefinitionNode?.type === 'agent' &&
    typeof selectedDefinitionNode.config.agent_id === 'string'
      ? selectedDefinitionNode.config.agent_id
      : ''

  return (
    <section className="platformPage workflowBuilderPage">
      <div className="workflowBuilderHeader">
        <div>
          <h2>Workflow Builder</h2>
          <p>可视化编排通用串行工作流。</p>
        </div>
        <div className="workflowBuilderHeaderStatus">
          <span>
            {selectedWorkflow === null
              ? '新建草稿'
              : selectedWorkflow.status === 'published'
                ? `已发布 · v${selectedWorkflow.version}`
                : `草稿 · v${selectedWorkflow.version}`}
          </span>
        </div>
      </div>

      {error !== null && (
        <p className="workflowBuilderError" role="alert">
          {error}
        </p>
      )}
      {notice !== null && (
        <p className="workflowBuilderNotice" role="status">
          {notice}
        </p>
      )}
      {!apiKeyConfigured ? (
        <p className="workflowBuilderError" role="status">
          需要 API Key 才能加载或保存流程。
        </p>
      ) : null}

      <div className="workflowBuilderLayout">
        <aside className="workflowBuilderSidebar" aria-label="流程列表">
          <div className="workflowBuilderSidebarHeader">
            <h3>流程</h3>
            <button type="button" className="secondaryButton" onClick={createNewWorkflow}>
              新建流程
            </button>
          </div>
          {loading && workflows.length === 0 ? <p>加载中…</p> : null}
          {!loading && workflows.length === 0 ? <p>暂无流程。</p> : null}
          <div className="workflowBuilderItems">
            {workflows.map((workflow) => (
              <button
                type="button"
                key={workflow.id}
                className={
                  workflow.id === selectedWorkflowId
                    ? 'workflowBuilderItem workflowBuilderItemActive'
                    : 'workflowBuilderItem'
                }
                onClick={() => void openWorkflow(workflow)}
              >
                <strong>{workflow.name}</strong>
                <span>
                  {workflow.status === 'published' ? '已发布' : '草稿'} · v{workflow.version}
                </span>
                <span>{formatTimestamp(workflow.updated_at)}</span>
              </button>
            ))}
          </div>
        </aside>

        <main className="workflowBuilderCanvasPanel">
          <form
            className="workflowBuilderMetaForm"
            onSubmit={(event) => {
              event.preventDefault()
              void saveWorkflow()
            }}
          >
            <label>
              流程名称
              <input
                value={name}
                placeholder="未命名流程"
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label>
              说明
              <input
                value={description}
                placeholder="可选"
                onChange={(event) => setDescription(event.target.value)}
              />
            </label>
            <div className="workflowBuilderActions">
              <button
                type="submit"
                className="secondaryButton"
                disabled={saving || !apiKeyConfigured}
              >
                保存草稿
              </button>
              <button
                type="button"
                className="secondaryButton"
                disabled={saving || selectedWorkflowId === null || !apiKeyConfigured}
                onClick={() => void publishWorkflow()}
              >
                {selectedWorkflow?.status === 'published' ? '已发布' : '发布'}
              </button>
              {selectedWorkflow?.status === 'published' ? (
                <button
                  type="button"
                  className="secondaryButton"
                  disabled={saving || !apiKeyConfigured}
                  onClick={() => void unpublishWorkflow()}
                >
                  取消发布
                </button>
              ) : null}
              <button
                type="button"
                className="dangerButton"
                disabled={saving || selectedWorkflow === null || !apiKeyConfigured}
                onClick={() => void deleteWorkflow()}
              >
                删除
              </button>
            </div>
          </form>

          <div className="workflowBuilderPalette" aria-label="节点面板">
            {WORKFLOW_BUILDER_NODE_TYPES.map((type) => (
              <button
                type="button"
                key={type}
                className="workflowBuilderPaletteItem"
                aria-label={`添加 ${WORKFLOW_BUILDER_NODE_LABELS[type]} 节点`}
                draggable
                onDragStart={(event) => {
                  event.dataTransfer.setData(NODE_DRAG_MIME, type)
                  event.dataTransfer.effectAllowed = 'move'
                }}
                onClick={() => addNodeAt(type, nextNodePosition(nodes))}
              >
                {WORKFLOW_BUILDER_NODE_LABELS[type]}
              </button>
            ))}
          </div>

          <ReactFlowProvider>
            <WorkflowCanvas
              nodes={nodes}
              edges={edges}
              onNodesChange={handleNodesChange}
              onEdgesChange={handleEdgesChange}
              onConnect={handleConnect}
              isValidConnection={handleConnectionCheck}
              onNodeClick={(_event, canvasNode) => selectNode(canvasNode.id)}
              onPaneClick={() => selectNode(null)}
              onNodesDelete={handleNodesDelete}
              onEdgesDelete={handleEdgesDelete}
              addNodeAt={addNodeAt}
            />
          </ReactFlowProvider>

          <section className="workflowBuilderRunPanel" aria-label="试运行">
            <div className="workflowBuilderRunHeader">
              <h3>试运行</h3>
              <label>
                输入 JSON
                <textarea
                  rows={3}
                  value={runInput}
                  onChange={(event) => setRunInput(event.target.value)}
                />
              </label>
              <button
                type="button"
                className="primaryButton"
                disabled={runLoading || selectedWorkflowId === null || !apiKeyConfigured}
                onClick={() => void runWorkflow()}
              >
                {runLoading ? '运行中…' : '开始试运行'}
              </button>
            </div>

            <div className="workflowBuilderRunContent">
              <aside className="workflowBuilderRunHistory" aria-label="运行历史">
                <h4>运行历史</h4>
                {runs.length === 0 ? <p>暂无运行记录。</p> : null}
                {runs.map((run) => (
                  <button
                    type="button"
                    key={run.id}
                    className={
                      selectedRun?.id === run.id
                        ? 'workflowBuilderRunItem workflowBuilderRunItemActive'
                        : 'workflowBuilderRunItem'
                    }
                    onClick={() => setSelectedRun(run)}
                  >
                    <strong>{run.status}</strong>
                    <span>{formatTimestamp(run.created_at)}</span>
                    <span>
                      {run.total_duration_ms === null ? '-' : `${run.total_duration_ms} ms`}
                    </span>
                  </button>
                ))}
              </aside>

              <div className="workflowBuilderRunDetail">
                {selectedRun === null ? (
                  <p className="workflowBuilderEmptyState">选择一条运行记录查看 node results。</p>
                ) : (
                  <>
                    <div className="workflowBuilderRunFacts">
                      <span>状态：{selectedRun.status}</span>
                      <span>耗时：{selectedRun.total_duration_ms ?? '-'} ms</span>
                      <span>开始：{formatTimestamp(selectedRun.created_at)}</span>
                    </div>
                    {selectedRun.error ? (
                      <p className="workflowBuilderError">{selectedRun.error}</p>
                    ) : null}
                    <ol className="workflowBuilderRunTimeline">
                      {selectedRun.node_results.map((result, index) => (
                        <li
                          key={`${result.node_id}-${index}`}
                          className={`workflowBuilderRunStep workflowBuilderRunStep-${result.status}`}
                        >
                          <span className="workflowBuilderRunStepIndex">{index + 1}</span>
                          <div>
                            <strong>
                              {WORKFLOW_BUILDER_NODE_LABELS[result.type]} · {result.node_id}
                            </strong>
                            <span>
                              {result.status === 'completed' ? '完成' : '失败'} ·{' '}
                              {result.duration_ms} ms
                            </span>
                            {result.input_summary ? <p>输入：{result.input_summary}</p> : null}
                            {result.output_summary ? <p>输出：{result.output_summary}</p> : null}
                            {result.error ? <p>{result.error}</p> : null}
                          </div>
                        </li>
                      ))}
                    </ol>
                  </>
                )}
              </div>
            </div>
          </section>
        </main>

        <aside className="workflowBuilderInspector" aria-label="节点配置">
          <h3>节点配置</h3>
          {selectedDefinitionNode === null ? (
            <p className="workflowBuilderEmptyState">选择一个节点。</p>
          ) : (
            <div className="workflowBuilderInspectorForm">
              <div className="workflowBuilderInspectorFacts">
                <span>{WORKFLOW_BUILDER_NODE_LABELS[selectedDefinitionNode.type]}</span>
                <span>{selectedDefinitionNode.id}</span>
              </div>
              <button
                type="button"
                className="dangerButton"
                onClick={() => removeNode(selectedDefinitionNode.id)}
              >
                删除节点
              </button>

              {selectedDefinitionNode.type === 'input' ? (
                <p>input 节点透传试运行输入变量。</p>
              ) : null}

              {selectedDefinitionNode.type === 'llm' ? (
                <>
                  <label>
                    模型
                    <input
                      value={
                        typeof selectedDefinitionNode.config.model === 'string'
                          ? selectedDefinitionNode.config.model
                          : ''
                      }
                      placeholder="如 qwen3:4b"
                      onChange={(event) => updateSelectedConfig({ model: event.target.value })}
                    />
                  </label>
                  <label>
                    System Prompt
                    <textarea
                      rows={4}
                      value={
                        typeof selectedDefinitionNode.config.system_prompt === 'string'
                          ? selectedDefinitionNode.config.system_prompt
                          : ''
                      }
                      onChange={(event) =>
                        updateSelectedConfig({ system_prompt: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    Prompt 模板
                    <textarea
                      rows={5}
                      value={
                        typeof selectedDefinitionNode.config.prompt_template === 'string'
                          ? selectedDefinitionNode.config.prompt_template
                          : ''
                      }
                      onChange={(event) =>
                        updateSelectedConfig({ prompt_template: event.target.value })
                      }
                    />
                  </label>
                </>
              ) : null}

              {selectedDefinitionNode.type === 'knowledge' ? (
                <label>
                  Query 模板
                  <textarea
                    rows={5}
                    value={
                      typeof selectedDefinitionNode.config.query_template === 'string'
                        ? selectedDefinitionNode.config.query_template
                        : ''
                    }
                    onChange={(event) =>
                      updateSelectedConfig({ query_template: event.target.value })
                    }
                  />
                </label>
              ) : null}

              {selectedDefinitionNode.type === 'tool' ? (
                <>
                  <label>
                    工具
                    <select
                      value={configTool}
                      onChange={(event) => updateSelectedConfig({ tool: event.target.value })}
                    >
                      <option value="">选择工具</option>
                      {configTool && !enabledTools.some((tool) => tool.name === configTool) ? (
                        <option value={configTool}>{configTool}（未启用）</option>
                      ) : null}
                      {enabledTools.map((tool) => (
                        <option key={tool.name} value={tool.name}>
                          {tool.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    参数模板 JSON
                    <textarea
                      className="workflowBuilderJsonInput"
                      rows={7}
                      value={toolArgumentsText}
                      onChange={updateToolArguments}
                    />
                  </label>
                  {toolArgumentsError ? (
                    <p className="workflowBuilderError">{toolArgumentsError}</p>
                  ) : null}
                </>
              ) : null}

              {selectedDefinitionNode.type === 'condition' ? (
                <>
                  <div className="workflowBuilderBranchList">
                    {(selectedDefinitionNode.config.branches ?? []).map((branch) => (
                      <div key={branch.id} className="workflowBuilderBranchRow">
                        <input
                          aria-label={`条件 ${branch.id}`}
                          value={branch.condition ?? ''}
                          placeholder="默认分支"
                          onChange={(event) =>
                            updateBranch(branch.id, {
                              condition: event.target.value.trim() ? event.target.value : null,
                            })
                          }
                        />
                        <select
                          aria-label={`分支目标 ${branch.id}`}
                          value={branch.target}
                          onChange={(event) =>
                            updateBranch(branch.id, { target: event.target.value })
                          }
                        >
                          <option value="">选择目标节点</option>
                          {branchTargetOptions.map((node) => (
                            <option key={node.id} value={node.id}>
                              {node.id}
                            </option>
                          ))}
                        </select>
                        <button
                          type="button"
                          className="dangerLink"
                          aria-label={`删除分支 ${branch.id}`}
                          onClick={() => removeBranch(branch.id)}
                        >
                          删除
                        </button>
                      </div>
                    ))}
                  </div>
                  <button type="button" className="secondaryButton" onClick={addBranch}>
                    添加分支
                  </button>
                </>
              ) : null}

              {selectedDefinitionNode.type === 'agent' ? (
                <>
                  <label>
                    Agent
                    <select
                      value={configAgent}
                      onChange={(event) => updateSelectedConfig({ agent_id: event.target.value })}
                    >
                      <option value="">选择 Agent</option>
                      {configAgent && !agents.some((agent) => agent.id === configAgent) ? (
                        <option value={configAgent}>{configAgent}（不存在）</option>
                      ) : null}
                      {agents.map((agent) => (
                        <option key={agent.id} value={agent.id}>
                          {agent.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Prompt 模板
                    <textarea
                      rows={5}
                      value={
                        typeof selectedDefinitionNode.config.prompt === 'string'
                          ? selectedDefinitionNode.config.prompt
                          : ''
                      }
                      onChange={(event) => updateSelectedConfig({ prompt: event.target.value })}
                    />
                  </label>
                </>
              ) : null}

              {selectedDefinitionNode.type === 'output' ? (
                <label>
                  输出模板
                  <textarea
                    rows={5}
                    value={
                      typeof selectedDefinitionNode.config.output_template === 'string'
                        ? selectedDefinitionNode.config.output_template
                        : ''
                    }
                    onChange={(event) =>
                      updateSelectedConfig({ output_template: event.target.value })
                    }
                  />
                </label>
              ) : null}
            </div>
          )}
        </aside>
      </div>
    </section>
  )
}
