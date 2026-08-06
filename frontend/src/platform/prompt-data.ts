export type PromptTemplate = {
  id: string
  name: string
  category: string
  description: string
  prompt: string
  example: string
}

export const STORAGE_KEY = 'ai-platform-mini.prompt-templates'

export const defaultTemplates: PromptTemplate[] = [
  {
    id: 'code-review',
    name: '代码审查助手',
    category: 'Engineering',
    description: '让模型用结构化方式分析代码风险、可维护性和改进建议。',
    prompt:
      '你是一名资深 Python 工程师。请从正确性、可维护性、性能和安全性四个维度审查用户提供的代码，并给出优先级明确的修改建议。',
    example: '帮我审查这段 FastAPI 依赖注入代码，并指出潜在的并发问题。',
  },
  {
    id: 'technical-summary',
    name: '技术文档总结',
    category: 'Knowledge',
    description: '把长篇技术内容压缩成适合团队同步的结构化摘要。',
    prompt:
      '你是一名技术文档编辑。请提取核心结论、关键术语、实现步骤和风险点，使用简洁的 Markdown 标题和列表组织回答。',
    example: '请总结这个项目的 Agent Runtime 设计，并列出一次请求的完整生命周期。',
  },
  {
    id: 'interview-coach',
    name: '面试模拟官',
    category: 'Career',
    description: '围绕项目技术细节进行追问，帮助准备 HR 或技术面演示。',
    prompt:
      '你是一名严格但友好的 AI 应用开发面试官。请先提出一个具体问题，等待回答后再从架构、工程实践和取舍三个角度追问。',
    example: '请模拟面试官，围绕我的 FastAPI + Ollama 项目开始提问。',
  },
  {
    id: 'rag-analysis',
    name: '知识库问答',
    category: 'RAG',
    description: '要求模型区分已有知识和检索上下文，避免生成无来源的引用。',
    prompt:
      '请优先基于检索到的知识库内容回答问题。如果来源不足，请明确说明信息缺口，不要编造引用或文档内容。',
    example: '请基于知识库解释项目当前的 RAG 安全边界。',
  },
]
