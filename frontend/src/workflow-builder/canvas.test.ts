import { describe, expect, it } from 'vitest'

import {
  canConnect,
  canvasToDefinition,
  createBuilderNode,
  createEmptyDefinition,
  definitionToCanvas,
  nextNodeId,
  nextNodePosition,
  validateDefinitionForSave,
  type BuilderCanvasNode,
} from './canvas.ts'

describe('workflow-builder canvas helpers', () => {
  it('creates a legal minimal input -> output definition', () => {
    const definition = createEmptyDefinition()

    expect(definition.nodes.map((node) => node.type)).toEqual(['input', 'output'])
    expect(definition.edges).toEqual([{ from: 'input-1', to: 'output-1' }])
    expect(validateDefinitionForSave('默认流程', definition)).toBeNull()
  })

  it('round-trips canvas nodes and edges with canvas positions', () => {
    const definition = createEmptyDefinition()
    const canvas = definitionToCanvas(definition)
    const restored = canvasToDefinition(canvas.nodes, canvas.edges)

    expect(restored.nodes).toHaveLength(2)
    expect(restored.nodes[0]?.config.canvas_position).toEqual({ x: 0, y: 0 })
    expect(restored.edges).toEqual([{ from: 'input-1', to: 'output-1' }])
  })

  it('generates unique node ids and next placement positions', () => {
    const first = createBuilderNode('llm', 'node-1', { x: 0, y: 0 })
    const second = createBuilderNode('tool', 'node-2', { x: 260, y: 80 })
    const canvasNodes: BuilderCanvasNode[] = [
      { id: first.id, type: first.type, position: { x: 0, y: 0 }, data: { node: first } },
      { id: second.id, type: second.type, position: { x: 260, y: 80 }, data: { node: second } },
    ]

    expect(nextNodeId(canvasNodes)).toBe('node-3')
    expect(nextNodePosition(canvasNodes)).toEqual({ x: 520, y: 80 })
  })

  it('rejects invalid ordinary connections', () => {
    const input = createBuilderNode('input', 'input-1', { x: 0, y: 0 })
    const llm = createBuilderNode('llm', 'llm-1', { x: 260, y: 0 })
    const output = createBuilderNode('output', 'output-1', { x: 520, y: 0 })
    const condition = createBuilderNode('condition', 'condition-1', { x: 260, y: 140 })

    expect(canConnect(input, output, [input, llm, output], [])).toBeNull()
    expect(
      canConnect(
        condition,
        output,
        [input, condition, output],
        [{ from: 'input-1', to: 'condition-1' }],
      ),
    ).toContain('普通连线')
    expect(
      canConnect(
        llm,
        output,
        [input, llm, output],
        [
          { from: 'input-1', to: 'llm-1' },
          { from: 'condition-1', to: 'output-1' },
        ],
      ),
    ).toContain('入边')
  })

  it('validates required node config and condition branch rules before save', () => {
    const definition = createEmptyDefinition()
    const llm = createBuilderNode('llm', 'llm-1', { x: 260, y: 0 })
    llm.config.prompt_template = ''
    const condition = createBuilderNode('condition', 'condition-1', { x: 520, y: 0 })
    condition.config.branches = [
      { id: 'b1', condition: null, target: 'output-1' },
      { id: 'b2', condition: null, target: 'output-1' },
      { id: 'b3', condition: '{{input.text}} ==', target: 'output-1' },
    ]
    definition.nodes = [definition.nodes[0]!, llm, condition, definition.nodes[1]!]
    definition.edges = [
      { from: 'input-1', to: 'llm-1' },
      { from: 'llm-1', to: 'condition-1' },
    ]

    const message = validateDefinitionForSave('流程', definition)

    expect(message).toContain('缺少 prompt_template')
    expect(message).toContain('只允许一个默认分支')
    expect(message).toContain('target output-1 重复')
    expect(message).toContain('表达式不合法')
  })

  it('rejects a cycle created by a condition branch target', () => {
    const definition = createEmptyDefinition()
    const condition = createBuilderNode('condition', 'condition-1', { x: 260, y: 0 })
    condition.config.branches = [{ id: 'b1', condition: null, target: 'input-1' }]
    definition.nodes = [definition.nodes[0]!, condition, definition.nodes[1]!]
    definition.edges = [{ from: 'input-1', to: 'condition-1' }]

    const message = validateDefinitionForSave('循环流程', definition)

    expect(message).toContain('存在环')
  })
})
