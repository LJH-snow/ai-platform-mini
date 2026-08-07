import { useEffect, useMemo, useState, type JSX } from 'react'

import {
  ConfigApiError,
  type ConfigClient,
  type UsageDashboard,
} from './config-client.ts'

type UsageDashboardProps = {
  client: ConfigClient
}

const CHART_WIDTH = 640
const CHART_HEIGHT = 180
const BAR_GAP = 6

const formatTokens = (tokens: number): string => {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(1)}K`
  return String(tokens)
}

function TrendChart({ trend }: { trend: UsageDashboard['trend'] }): JSX.Element {
  const geometry = useMemo(() => {
    const max = Math.max(1, ...trend.map((point) => point.total_tokens))
    const step = trend.length > 1 ? (CHART_WIDTH - BAR_GAP) / trend.length : CHART_WIDTH
    return {
      max,
      step,
      barWidth: Math.max(2, step - BAR_GAP),
    }
  }, [trend])

  if (trend.length === 0) {
    return <p className="chartEmpty">近 N 日暂无用量数据。</p>
  }

  const bars = trend.map((point, index) => {
    const height = Math.max(2, (point.total_tokens / geometry.max) * CHART_HEIGHT)
    const x = index * geometry.step
    return (
      <g key={point.usage_date}>
        <rect
          x={x}
          y={CHART_HEIGHT - height}
          width={geometry.barWidth}
          height={height}
          fill="var(--accent, #3b82f6)"
          rx="2"
        />
        <text
          x={x + geometry.barWidth / 2}
          y={CHART_HEIGHT - height - 4}
          textAnchor="middle"
          fontSize="10"
          fill="currentColor"
        >
          {formatTokens(point.total_tokens)}
        </text>
      </g>
    )
  })

  return (
    <svg
      className="trendChart"
      viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT + 18}`}
      role="img"
      aria-label="每日 Token 用量趋势"
    >
      {bars}
      {trend.map((point, index) => (
        <text
          key={point.usage_date}
          x={index * geometry.step + geometry.barWidth / 2}
          y={CHART_HEIGHT + 14}
          textAnchor="middle"
          fontSize="10"
          fill="currentColor"
        >
          {point.usage_date.slice(5)}
        </text>
      ))}
    </svg>
  )
}

function RankingList({
  title,
  entries,
}: {
  title: string
  entries: UsageDashboard['model_ranking']
}): JSX.Element {
  const max = Math.max(1, ...entries.map((entry) => entry.total_tokens))
  return (
    <div className="rankingCard">
      <h3>{title}</h3>
      {entries.length === 0 && <p>暂无数据。</p>}
      <ul className="rankingList">
        {entries.map((entry) => (
          <li key={entry.name}>
            <div className="rankingRow">
              <span className="rankingName">{entry.name}</span>
              <span className="rankingValue">{formatTokens(entry.total_tokens)}</span>
            </div>
            <div
              className="rankingBar"
              role="progressbar"
              aria-label={`${entry.name} 占比`}
              aria-valuenow={Math.round((entry.total_tokens / max) * 100)}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <span
                style={{ width: `${Math.max(2, (entry.total_tokens / max) * 100)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function UsageDashboardPage({ client }: UsageDashboardProps): JSX.Element {
  const [data, setData] = useState<UsageDashboard | null>(null)
  const [days, setDays] = useState(7)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    client
      .getUsageDashboard(days)
      .then((dashboard) => {
        if (cancelled) return
        setData(dashboard)
      })
      .catch((caught: unknown) => {
        if (cancelled) return
        setError(
          caught instanceof ConfigApiError ? caught.message : '用量数据加载失败。',
        )
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [client, days])

  return (
    <section className="platformPage">
      <div className="pageHeader">
        <h2>Usage Dashboard</h2>
        <label className="daysPicker">
          时间范围：
          <select
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
          >
            <option value={7}>近 7 天</option>
            <option value={14}>近 14 天</option>
            <option value={30}>近 30 天</option>
          </select>
        </label>
      </div>

      {loading && <p>加载中…</p>}
      {error !== null && <p className="inlineError" role="alert">{error}</p>}

      {data !== null && (
        <>
          <div className="trendCard">
            <h3>每日 Token 用量</h3>
            <TrendChart trend={data.trend} />
          </div>
          <div className="rankingGrid">
            <RankingList title="按模型" entries={data.model_ranking} />
            <RankingList title="按 Key" entries={data.key_ranking} />
          </div>
        </>
      )}
    </section>
  )
}
