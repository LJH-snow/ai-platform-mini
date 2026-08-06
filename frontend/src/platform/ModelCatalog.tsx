import { useCallback, useEffect, useState, type JSX } from 'react'

import { PlatformApiError, type PlatformClient } from './client.ts'
import type { PlatformModel } from './types.ts'

type ModelCatalogProps = {
  apiKeyConfigured: boolean
  client: PlatformClient
}

export function ModelCatalog({ apiKeyConfigured, client }: ModelCatalogProps): JSX.Element {
  const [models, setModels] = useState<PlatformModel[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const loadModels = useCallback(async (): Promise<void> => {
    setLoading(true)
    setError(null)
    try {
      setModels(await client.listModels())
    } catch (caught) {
      setModels(null)
      setError(caught instanceof PlatformApiError ? caught.message : '模型目录加载失败。')
    } finally {
      setLoading(false)
    }
  }, [client])

  useEffect(() => {
    if (apiKeyConfigured) void loadModels()
    else setModels(null)
  }, [apiKeyConfigured, loadModels])

  return (
    <section className="platformPage" aria-labelledby="models-title">
      <div className="pageIntro compactPageIntro">
        <div>
          <p className="pageKicker">MODEL CATALOG · PROVIDERS</p>
          <h1 id="models-title">模型目录</h1>
          <p className="pageLead">
            从 Gateway
            读取真实可用模型。模型启停和删除不在当前后端能力范围内，因此页面只展示可验证的信息。
          </p>
        </div>
        <button
          type="button"
          className="secondaryButton"
          onClick={() => void loadModels()}
          disabled={loading || !apiKeyConfigured}
        >
          {loading ? '刷新中…' : '刷新目录'}
        </button>
      </div>

      {!apiKeyConfigured ? (
        <div className="platformNotice platformNotice-warning" role="status">
          <strong>需要普通用户 API Key</strong>
          <span>请在对话工作台配置管理员创建的普通 Key，再查看真实模型目录。</span>
        </div>
      ) : null}
      {error ? (
        <div className="platformNotice platformNotice-error" role="alert">
          <strong>模型目录暂时不可用</strong>
          <span>{error}</span>
        </div>
      ) : null}

      {apiKeyConfigured && models && models.length === 0 ? (
        <div className="platformEmptyState">
          <span className="emptyStateGlyph" aria-hidden="true">
            ∅
          </span>
          <h2>暂时没有可用模型</h2>
          <p>请确认 Ollama 已启动，并且本地已经拉取至少一个模型。</p>
        </div>
      ) : null}

      {models && models.length > 0 ? (
        <div className="modelCatalogGrid" aria-label="可用模型列表">
          {models.map((model, index) => (
            <article className="modelCard" key={model.id}>
              <div className="modelCardHeader">
                <span className="modelIndex">0{index + 1}</span>
                <span className="modelLiveBadge">AVAILABLE</span>
              </div>
              <h2>{model.id}</h2>
              <div className="modelFacts">
                <span>
                  <small>Provider</small>
                  {model.provider}
                </span>
                <span>
                  <small>Capabilities</small>
                  Chat · Agent · SSE
                </span>
              </div>
              <div className="modelCardFooter">
                <span className="modelPulse" aria-hidden="true" />
                可用于当前工作台
              </div>
            </article>
          ))}
        </div>
      ) : null}

      <section className="capabilityStrip" aria-label="模型能力说明">
        <div>
          <span className="sectionKicker">WHY THIS PAGE</span>
          <h2>模型是可替换的基础设施</h2>
        </div>
        <p>
          前端不把 qwen3:4b 写死在界面里，而是通过统一的 Models API 读取 Provider
          返回结果。切换本地模型或 OpenAI-compatible Provider 时，平台页面仍然可以复用。
        </p>
      </section>
    </section>
  )
}
