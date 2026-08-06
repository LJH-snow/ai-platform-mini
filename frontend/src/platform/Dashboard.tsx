import type { JSX } from 'react'
import type { RagRuntimeStatus } from './rag-status.ts'

type DashboardProps = {
  apiKeyConfigured: boolean
  modelCount: number | null
  modelName: string | null
  ragStatus: RagRuntimeStatus
  onNavigate: (page: 'console' | 'knowledge' | 'prompts' | 'models', preset?: 'agent') => void
}

type QuickStart = {
  page: 'console' | 'knowledge' | 'prompts'
  eyebrow: string
  title: string
  description: string
  action: string
  tone: string
  preset?: 'agent'
}

const ragCopy = '通过受限 RAG 来源展示检索增强生成，来源状态和片段均来自后端安全投影。'

const quickStarts: QuickStart[] = [
  {
    page: 'console',
    eyebrow: '01 · Chat SSE',
    title: '智能问答',
    description: '用真实流式响应解释技术问题，适合展示基础 LLM Gateway 能力。',
    action: '打开对话工作台',
    tone: 'blue',
  },
  {
    page: 'console',
    eyebrow: '02 · Agent Runtime',
    title: 'Agent 工作流',
    description: '观察模型决策、工具调用、运行状态和最终回答如何串成一条 Trace。',
    action: '运行 Agent Demo',
    tone: 'violet',
    preset: 'agent',
  },
  {
    page: 'knowledge',
    eyebrow: '03 · RAG',
    title: '知识库问答',
    description: ragCopy,
    action: '进入知识库演示',
    tone: 'green',
  },
  {
    page: 'prompts',
    eyebrow: '04 · Prompt Engineering',
    title: 'Prompt Studio',
    description: '编辑和保存常用提示词模板，一键带入真实对话流程。',
    action: '打开 Prompt Studio',
    tone: 'orange',
  },
]

const ragStatusCard = (
  status: RagRuntimeStatus,
): { value: string; detail: string; state: 'online' | 'pending' | 'offline' } => {
  switch (status.kind) {
    case 'loading':
      return { value: '正在检查 RAG', detail: '等待后端能力探测', state: 'pending' }
    case 'ready':
      return {
        value: 'RAG 已启用',
        detail: status.embeddingModel ? `Embedding ${status.embeddingModel}` : '检索来源可审计',
        state: 'online',
      }
    case 'disabled':
      return { value: 'RAG 未启用', detail: '启用后展示真实来源', state: 'pending' }
    case 'database_unavailable':
      return { value: '知识库数据库不可用', detail: '暂无法确认或操作知识库', state: 'offline' }
    case 'embedding_unavailable':
      return { value: 'Embedding 服务不可用', detail: '暂无法上传或检索文档', state: 'offline' }
    case 'unavailable':
      return { value: 'RAG 暂不可用', detail: '能力探测未通过', state: 'offline' }
    case 'error':
      return { value: 'RAG 状态未知', detail: '健康检查失败', state: 'offline' }
  }
}

const statusCards = (props: DashboardProps) => [
  {
    label: 'API Gateway',
    value: props.apiKeyConfigured ? '已配置访问凭证' : '等待配置 API Key',
    detail: 'FastAPI · OpenAI-compatible',
    state: props.apiKeyConfigured ? 'online' : 'pending',
  },
  {
    label: 'Model Provider',
    value: props.modelName ?? '等待读取模型',
    detail: props.modelCount === null ? '模型目录未加载' : `${props.modelCount} 个可用模型`,
    state: props.modelCount === null ? 'pending' : 'online',
  },
  {
    label: 'Agent Runtime',
    value: '可观察执行链路',
    detail: 'SSE · Tool Call · Trace',
    state: 'online',
  },
  {
    label: 'Knowledge Base',
    ...ragStatusCard(props.ragStatus),
  },
]

export function Dashboard(props: DashboardProps): JSX.Element {
  const cards = statusCards(props)

  return (
    <section className="platformPage dashboardPage" aria-labelledby="dashboard-title">
      <div className="pageIntro dashboardIntro">
        <div>
          <p className="pageKicker">AI APPLICATION PLATFORM · OVERVIEW</p>
          <h1 id="dashboard-title">把模型能力，变成可观察的应用。</h1>
          <p className="pageLead">
            AI Platform Mini 是一个轻量级大模型应用平台：从流式对话到 Agent Runtime，再到 RAG
            来源审计，所有演示都连接真实的后端能力。
          </p>
        </div>
        <div className="introBadge">
          <span
            className={
              props.apiKeyConfigured ? 'introBadgeDot' : 'introBadgeDot introBadgeDotPending'
            }
            aria-hidden="true"
          />
          <span>本地开发环境</span>
          <strong>{props.apiKeyConfigured ? 'Ready to demo' : 'Key required'}</strong>
        </div>
      </div>

      <div className="statusCardGrid" aria-label="平台能力状态">
        {cards.map((card) => (
          <article className="platformStatusCard" key={card.label}>
            <div className={`statusDot statusDot-${card.state}`} aria-hidden="true" />
            <div>
              <span className="statusCardLabel">{card.label}</span>
              <strong>{card.value}</strong>
              <span className="statusCardDetail">{card.detail}</span>
            </div>
          </article>
        ))}
      </div>

      <div className="sectionHeading">
        <div>
          <p className="sectionKicker">DEMO PATHS</p>
          <h2>从一个入口，讲清楚四种 AI 能力</h2>
        </div>
        <span className="sectionHint">建议演示时从左到右进行</span>
      </div>

      <div className="quickStartGrid">
        {quickStarts.map((item) => (
          <article className={`quickStartCard quickStart-${item.tone}`} key={item.title}>
            <span className="quickStartEyebrow">{item.eyebrow}</span>
            <h3>{item.title}</h3>
            <p>{item.description}</p>
            <button type="button" onClick={() => props.onNavigate(item.page, item.preset)}>
              {item.action}
              <span aria-hidden="true">→</span>
            </button>
          </article>
        ))}
      </div>

      <section className="architectureCard" aria-labelledby="architecture-title">
        <div>
          <p className="sectionKicker">SYSTEM DESIGN</p>
          <h2 id="architecture-title">从请求到回答，每一层都能解释。</h2>
          <p>
            前端只负责交互和可视化；Gateway 负责鉴权、限流和兼容协议；Agent Runtime
            负责决策与工具编排；Provider 和知识库提供可替换的模型与检索能力。
          </p>
        </div>
        <div className="architectureFlow" aria-label="系统架构流程">
          <span>React UI</span>
          <i aria-hidden="true">→</i>
          <span>FastAPI Gateway</span>
          <i aria-hidden="true">→</i>
          <span>Agent Runtime</span>
          <i aria-hidden="true">→</i>
          <span>Ollama / LLM</span>
        </div>
      </section>
    </section>
  )
}
