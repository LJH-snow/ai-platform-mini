import { Handle, Position, type NodeProps } from '@xyflow/react'
import type { AgentNodeData } from './types.ts'
import { ROLE_LABELS, ROLE_COLORS } from './types.ts'

export type AgentNodeProps = NodeProps & {
  data: AgentNodeData
}

const STATUS_STYLES: Record<string, string> = {
  pending: '#94a3b8',
  running: '#3b82f6',
  completed: '#22c55e',
  failed: '#ef4444',
  cancelled: '#f97316',
}

export function AgentNode({ data, selected }: AgentNodeProps) {
  const roleColor = ROLE_COLORS[data.role] ?? ROLE_COLORS.custom
  const statusColor = data.status ? STATUS_STYLES[data.status] : undefined
  const roleLabel = ROLE_LABELS[data.role] ?? data.role

  return (
    <div
      className="agentNode"
      style={{
        border: selected ? '2px solid #2563eb' : '1px solid #e2e8f0',
        borderLeft: `4px solid ${roleColor}`,
        boxShadow: statusColor ? `0 0 0 2px ${statusColor}40` : undefined,
      }}
    >
      <Handle type="target" position={Position.Top} />
      <div className="agentNodeHeader">
        <span className="agentNodeRole" style={{ background: roleColor }}>
          {roleLabel}
        </span>
        {data.status && (
          <span className="agentNodeStatus" style={{ color: statusColor }}>
            {data.status}
          </span>
        )}
      </div>
      <div className="agentNodeLabel">{data.label}</div>
      {data.description && <div className="agentNodeDesc">{data.description}</div>}
      <Handle type="source" position={Position.Bottom} />
    </div>
  )
}
