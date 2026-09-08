import { useCallback, useMemo, useState } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  applyNodeChanges,
  applyEdgeChanges,
  type Node,
  type Edge,
  type Connection,
  type NodeChange,
  type EdgeChange,
  type NodeTypes,
  BackgroundVariant,
  type NodeMouseHandler,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { AgentNode } from './AgentNode.tsx'
import type { CanvasDAG, CanvasConfig } from './client.ts'
import type { AgentNodeData, AgentFlowNode } from './types.ts'
import { ROLE_LABELS } from './types.ts'
import './canvas.css'

type CanvasPageProps = {
  client: {
    listConfigs: () => Promise<CanvasConfig[]>
    getConfig: (id: string) => Promise<CanvasConfig>
    createConfig: (input: {
      name: string
      description?: string
      dag: CanvasDAG
      orchestrationConfig?: Record<string, unknown>
    }) => Promise<CanvasConfig>
    updateConfig: (
      id: string,
      input: {
        name?: string
        description?: string
        dag?: CanvasDAG
        orchestrationConfig?: Record<string, unknown>
      },
    ) => Promise<CanvasConfig>
    deleteConfig: (id: string) => Promise<void>
  }
  onRunConfig: (configId: string, userInput: string) => void
}

const NODE_TYPES: NodeTypes = { agent: AgentNode }

const createNewNode = (index: number, position: { x: number; y: number }): AgentFlowNode => ({
  id: `task_${Date.now()}_${index}`,
  type: 'agent',
  position,
  data: {
    label: `Task ${index}`,
    role: 'research',
    description: '',
    config: {},
  },
})

export function CanvasPage({ client, onRunConfig }: CanvasPageProps) {
  const [nodes, setNodes] = useState<AgentFlowNode[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [selectedNode, setSelectedNode] = useState<AgentFlowNode | null>(null)
  const [configName, setConfigName] = useState('Untitled Pipeline')
  const [configDescription, _setConfigDescription] = useState('')
  const [savedConfigId, setSavedConfigId] = useState<string | null>(null)
  const [failurePolicy, setFailurePolicy] = useState('fail_fast')
  const [maxConcurrency, setMaxConcurrency] = useState(3)
  const [totalTimeout, setTotalTimeout] = useState(300)
  const [statusMessage, setStatusMessage] = useState('')

  const flowNodes: Node[] = useMemo(() => nodes as Node[], [nodes])

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((nds) => applyNodeChanges(changes, nds) as AgentFlowNode[])
  }, [])

  const onEdgesChange = useCallback((changes: EdgeChange[]) => {
    setEdges((eds) => applyEdgeChanges(changes, eds))
  }, [])

  const onConnect = useCallback((connection: Connection) => {
    setEdges((eds) => addEdge({ ...connection, type: 'smoothstep' }, eds))
  }, [])

  const handleNodeClick: NodeMouseHandler = useCallback((_event, node) => {
    setSelectedNode(node as AgentFlowNode)
  }, [])

  const handlePaneClick = useCallback(() => {
    setSelectedNode(null)
  }, [])

  const addNode = useCallback(() => {
    const newNode = createNewNode(nodes.length + 1, {
      x: 100 + (nodes.length % 3) * 200,
      y: 100 + Math.floor(nodes.length / 3) * 150,
    })
    setNodes((nds) => [...nds, newNode])
  }, [nodes.length])

  const updateSelectedNode = useCallback(
    (updates: Partial<AgentNodeData>) => {
      if (!selectedNode) return
      setNodes((nds) =>
        nds.map((n) => (n.id === selectedNode.id ? { ...n, data: { ...n.data, ...updates } } : n)),
      )
      setSelectedNode((prev) => (prev ? { ...prev, data: { ...prev.data, ...updates } } : null))
    },
    [selectedNode],
  )

  const deleteSelectedNode = useCallback(() => {
    if (!selectedNode) return
    setNodes((nds) => nds.filter((n) => n.id !== selectedNode.id))
    setEdges((eds) =>
      eds.filter((e) => e.source !== selectedNode.id && e.target !== selectedNode.id),
    )
    setSelectedNode(null)
  }, [selectedNode])

  const dag: CanvasDAG = useMemo(
    () => ({
      nodes: nodes.map((n) => ({
        id: n.id,
        role: n.data.role,
        description: n.data.description,
        config: n.data.config,
        position: n.position,
      })),
      edges: edges.map((e) => ({ id: e.id, source: e.source, target: e.target })),
    }),
    [nodes, edges],
  )

  const saveConfig = useCallback(async () => {
    try {
      const orchestrationConfig = {
        failure_policy: failurePolicy,
        max_concurrency: maxConcurrency,
        total_timeout: totalTimeout,
      }
      if (savedConfigId) {
        await client.updateConfig(savedConfigId, {
          name: configName,
          description: configDescription || undefined,
          dag,
          orchestrationConfig,
        })
        setStatusMessage('Configuration updated.')
      } else {
        const created = await client.createConfig({
          name: configName,
          description: configDescription || undefined,
          dag,
          orchestrationConfig,
        })
        setSavedConfigId(created.id)
        setStatusMessage('Configuration saved.')
      }
    } catch (error) {
      setStatusMessage(`Save failed: ${error instanceof Error ? error.message : 'Unknown error'}`)
    }
  }, [
    client,
    configName,
    configDescription,
    dag,
    savedConfigId,
    failurePolicy,
    maxConcurrency,
    totalTimeout,
  ])

  const runConfig = useCallback(() => {
    if (!savedConfigId) {
      setStatusMessage('Please save the configuration before running.')
      return
    }
    onRunConfig(savedConfigId, 'Run from canvas')
  }, [savedConfigId, onRunConfig])

  return (
    <div className="canvasPage">
      <div className="canvasToolbar">
        <input
          className="canvasConfigName"
          value={configName}
          onChange={(e) => setConfigName(e.target.value)}
          placeholder="Configuration name"
        />
        <button type="button" className="secondaryButton" onClick={addNode}>
          + Add Node
        </button>
        <button type="button" className="primaryButton" onClick={saveConfig}>
          Save
        </button>
        <button type="button" className="primaryButton" onClick={runConfig}>
          Run
        </button>
        {statusMessage && <span className="canvasStatus">{statusMessage}</span>}
      </div>
      <div className="canvasBody">
        <div className="canvasFlow">
          <ReactFlow
            nodes={flowNodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onNodeClick={handleNodeClick}
            onPaneClick={handlePaneClick}
            nodeTypes={NODE_TYPES}
            fitView
          >
            <Background variant={BackgroundVariant.Dots} gap={16} size={1} />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </div>
        <div className="canvasSidebar">
          {selectedNode ? (
            <div className="nodeConfigPanel">
              <h3>Node Configuration</h3>
              <label>
                Label
                <input
                  value={selectedNode.data.label}
                  onChange={(e) => updateSelectedNode({ label: e.target.value })}
                />
              </label>
              <label>
                Role
                <select
                  value={selectedNode.data.role}
                  onChange={(e) => updateSelectedNode({ role: e.target.value })}
                >
                  {Object.entries(ROLE_LABELS).map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Description
                <textarea
                  value={selectedNode.data.description}
                  onChange={(e) => updateSelectedNode({ description: e.target.value })}
                  rows={3}
                />
              </label>
              <label>
                Model
                <input
                  value={selectedNode.data.config.model ?? ''}
                  onChange={(e) =>
                    updateSelectedNode({
                      config: { ...selectedNode.data.config, model: e.target.value || null },
                    })
                  }
                  placeholder="e.g. gpt-4o"
                />
              </label>
              <label>
                Max Steps
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={selectedNode.data.config.maxSteps ?? ''}
                  onChange={(e) =>
                    updateSelectedNode({
                      config: {
                        ...selectedNode.data.config,
                        maxSteps: e.target.value ? Number(e.target.value) : null,
                      },
                    })
                  }
                />
              </label>
              <button type="button" className="dangerButton" onClick={deleteSelectedNode}>
                Delete Node
              </button>
            </div>
          ) : (
            <div className="orchestrationPanel">
              <h3>Orchestration Settings</h3>
              <label>
                Failure Policy
                <select value={failurePolicy} onChange={(e) => setFailurePolicy(e.target.value)}>
                  <option value="fail_fast">Fail Fast</option>
                  <option value="skip">Skip</option>
                </select>
              </label>
              <label>
                Max Concurrency
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={maxConcurrency}
                  onChange={(e) => setMaxConcurrency(Number(e.target.value))}
                />
              </label>
              <label>
                Total Timeout (seconds)
                <input
                  type="number"
                  min={1}
                  value={totalTimeout}
                  onChange={(e) => setTotalTimeout(Number(e.target.value))}
                />
              </label>
              <p className="hint">Click a node to edit its configuration.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
