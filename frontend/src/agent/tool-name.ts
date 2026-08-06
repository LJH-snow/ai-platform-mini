const KNOWN_TOOLS = new Set(['calculator', 'knowledge_search'])

const BUILT_IN_TOOL_NAMES: Record<string, string> = {
  calculator: '计算器',
  knowledge_search: '知识搜索',
}

// Add precise MCP labels here when a friendly display name is required.
// A bare `mcp__` uses a generic label; other fallbacks only format
// unambiguous `mcp__server__tool` names.
const MCP_TOOL_NAME_MAP: Record<string, string> = {
  'mcp__docs-server__search_docs': '文档搜索',
}

const MCP_PREFIX = 'mcp__'
const MCP_SEPARATOR = '__'

/**
 * `known` means the backend sent a built-in tool name or an MCP-style
 * `mcp__...` name, so the UI should not label it unknown. It does not verify
 * that the tool is registered in this frontend build.
 */
export const isKnownTool = (name: string): boolean =>
  KNOWN_TOOLS.has(name) || name.startsWith(MCP_PREFIX)

export const localizeToolName = (name: string): string => {
  if (Object.hasOwn(BUILT_IN_TOOL_NAMES, name)) return BUILT_IN_TOOL_NAMES[name]
  if (Object.hasOwn(MCP_TOOL_NAME_MAP, name)) return MCP_TOOL_NAME_MAP[name]
  if (!name.startsWith(MCP_PREFIX)) return name

  const rest = name.slice(MCP_PREFIX.length)
  // A bare prefix has no server/tool parts to parse, so use a generic label.
  if (!rest) return 'MCP 工具'
  const separatorIndex = rest.indexOf(MCP_SEPARATOR)
  const server = rest.slice(0, separatorIndex)
  const tool = rest.slice(separatorIndex + MCP_SEPARATOR.length)
  if (server.length === 0 || tool.length === 0 || tool.includes(MCP_SEPARATOR)) return name
  return `MCP 工具：${tool}（${server}）`
}
