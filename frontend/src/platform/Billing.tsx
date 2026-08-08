import { useEffect, useState, type JSX } from 'react'

import {
  ConfigApiError,
  type BillingInfo,
  type ConfigClient,
} from './config-client.ts'

type BillingProps = {
  client: ConfigClient
}

const formatTokens = (tokens: number): string => {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`
  return String(tokens)
}

function UsageBar({
  used,
  limit,
}: {
  used: number
  limit: number | null
}): JSX.Element {
  if (limit === null || limit <= 0) {
    return <p className="billingUnlimited">不限</p>
  }
  const percent = Math.min(100, Math.round((used / limit) * 100))
  return (
    <div className="billingUsageBar">
      <div
        className="billingUsageFill"
        role="progressbar"
        aria-label="月度 Token 用量"
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
        style={{ width: `${Math.max(2, percent)}%` }}
      />
      <span>
        {formatTokens(used)} / {formatTokens(limit)}（{percent}%）
      </span>
    </div>
  )
}

function ResourceRow({
  label,
  resource,
}: {
  label: string
  resource: { count: number; limit: number | null }
}): JSX.Element {
  const limitText = resource.limit === null ? '不限' : String(resource.limit)
  const over = resource.limit !== null && resource.count > resource.limit
  return (
    <div className="billingResourceRow">
      <span>{label}</span>
      <span className={over ? 'billingOver' : undefined}>
        {resource.count} / {limitText}
        {over ? '（超限）' : ''}
      </span>
    </div>
  )
}

export function Billing({ client }: BillingProps): JSX.Element {
  const [billing, setBilling] = useState<BillingInfo | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    client
      .getBilling()
      .then((info) => {
        if (!cancelled) setBilling(info)
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(
            caught instanceof ConfigApiError ? caught.message : 'Billing 信息加载失败。',
          )
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client])

  const plan = billing?.plan ?? null

  return (
    <section className="platformPage">
      <div className="pageHeader">
        <h2>Billing / 计划</h2>
        <p>当前计划与用量；无订阅 = legacy 模式（不限）。</p>
      </div>

      {loading && <p>加载中…</p>}
      {error !== null && <p className="inlineError" role="alert">{error}</p>}

      {billing !== null && (
        <>
          <div className="billingPlanCard">
            {plan === null ? (
              <>
                <h3>无计划（legacy）</h3>
                <p>当前 workspace 未订阅任何计划，配额与资源不受限（继承全局默认）。</p>
              </>
            ) : (
              <>
                <h3>
                  {plan.name}（{plan.status}）
                </h3>
                <p>
                  {plan.monthly_token_limit === null
                    ? '月度 Token：不限'
                    : `月度 Token：${formatTokens(plan.monthly_token_limit)}`}
                  {plan.daily_token_limit !== null &&
                    ` · 每日 ${formatTokens(plan.daily_token_limit)}`}
                </p>
              </>
            )}
          </div>

          <div className="billingSection">
            <h3>本月用量</h3>
            <UsageBar
              used={billing.usage.total_tokens}
              limit={plan?.monthly_token_limit ?? null}
            />
          </div>

          <div className="billingSection">
            <h3>资源</h3>
            <ResourceRow label="Agents" resource={billing.resources.agents} />
            <ResourceRow label="文档" resource={billing.resources.documents} />
            <ResourceRow label="成员" resource={billing.resources.members} />
          </div>

          {plan !== null && Object.keys(plan.features).length > 0 && (
            <div className="billingSection">
              <h3>功能</h3>
              <ul className="billingFeatures">
                {Object.entries(plan.features).map(([feature, enabled]) => (
                  <li key={feature}>
                    <span className={enabled ? undefined : 'billingFeatureOff'}>
                      {feature}
                    </span>
                    {enabled ? '已启用' : '未启用'}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  )
}
