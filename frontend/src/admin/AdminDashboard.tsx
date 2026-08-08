import type { FormEvent, JSX } from 'react'
import { useMemo, useState } from 'react'
import type { AuditEvent } from './client.ts'
import { AdminApiError, createAdminClient, type AdminClient } from './client.ts'
import type {
  AdminApiKey,
  AgentRunRecord,
  AgentRunSummary,
  CreatedAdminApiKey,
  UsageAggregation,
} from './types.ts'
import { formatAgentTimestamp } from '../agent/time.ts'

type UsagePeriod = 'daily' | 'monthly'

type AdminDashboardProps = {
  apiBaseUrl?: string
  onBack: () => void
}

const ADMIN_KEY_STORAGE = 'ai-platform-admin-key'
const USER_KEY_STORAGE = 'ai-platform-user-key'

const ShanghaiDateTime = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
})

const todayInShanghai = (): string => {
  const parts = Object.fromEntries(
    ShanghaiDateTime.formatToParts(new Date()).map(({ type, value }) => [type, value]),
  )
  return `${parts.year}-${parts.month}-${parts.day}`
}

const monthInShanghai = (): string => todayInShanghai().slice(0, 7)

const formatNumber = (value: number | null | undefined): string =>
  value === null || value === undefined ? '—' : value.toLocaleString('zh-CN')

const formatStatus = (status: string): string => {
  const labels: Record<string, string> = {
    active: '有效',
    revoked: '已撤销',
    completed: '已完成',
    failed: '失败',
    timed_out: '超时',
    cancelled: '已取消',
    stopped: '已停止',
  }
  return labels[status] ?? status
}

const runDetails = (run: AgentRunRecord): string => {
  const response = run.response
  const answer = typeof response.answer === 'string' ? response.answer : '（没有回答文本）'
  return answer
}

export function AdminDashboard({ apiBaseUrl, onBack }: AdminDashboardProps): JSX.Element {
  const [adminKey, setAdminKey] = useState(() => sessionStorage.getItem(ADMIN_KEY_STORAGE) ?? '')
  const [client, setClient] = useState<AdminClient | null>(null)
  const [keys, setKeys] = useState<AdminApiKey[]>([])
  const [runs, setRuns] = useState<AgentRunSummary[]>([])
  const [selectedRun, setSelectedRun] = useState<AgentRunRecord | null>(null)
  const [selectedPrefix, setSelectedPrefix] = useState('')
  const [usage, setUsage] = useState<UsageAggregation[]>([])
  const [usageDate, setUsageDate] = useState(todayInShanghai)
  const [quotaWorkspaceId, setQuotaWorkspaceId] = useState('')
  const [quotaDaily, setQuotaDaily] = useState('')
  const [quotaMonthly, setQuotaMonthly] = useState('')
  const [quotaNotice, setQuotaNotice] = useState<string | null>(null)
  const [quotaError, setQuotaError] = useState<string | null>(null)
  const [auditEvents, setAuditEvents] = useState<AuditEvent[] | null>(null)
  const [auditError, setAuditError] = useState<string | null>(null)
  const [usageMonth, setUsageMonth] = useState(monthInShanghai)
  const [usagePeriod, setUsagePeriod] = useState<UsagePeriod>('daily')
  const [newKeyName, setNewKeyName] = useState('HR 演示用户')
  const [createdKey, setCreatedKey] = useState<CreatedAdminApiKey | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const ordinaryKeys = useMemo(() => keys.filter((item) => item.is_admin !== true), [keys])

  const totals = useMemo(
    () =>
      usage.reduce(
        (result, item) => ({
          requests: result.requests + item.request_count,
          prompt: result.prompt + item.prompt_tokens,
          completion: result.completion + item.completion_tokens,
          total: result.total + item.total_tokens,
        }),
        { requests: 0, prompt: 0, completion: 0, total: 0 },
      ),
    [usage],
  )

  const loadDashboard = async (nextClient: AdminClient, nextKeys?: AdminApiKey[]) => {
    setLoading(true)
    setError(null)
    try {
      const currentKeys = nextKeys ?? (await nextClient.listKeys())
      const ordinaryKeys = currentKeys.filter((item) => item.is_admin !== true)
      const nextPrefix =
        selectedPrefix && ordinaryKeys.some((item) => item.key_hash_prefix === selectedPrefix)
          ? selectedPrefix
          : ((ordinaryKeys.find((item) => item.status === 'active') ?? ordinaryKeys[0])
              ?.key_hash_prefix ?? '')
      setKeys(currentKeys)
      setSelectedPrefix(nextPrefix)
      const [nextRuns, nextUsage] = await Promise.all([
        nextClient.listRuns(),
        nextPrefix
          ? usagePeriod === 'monthly'
            ? nextClient.getMonthlyUsage(nextPrefix, usageMonth)
            : nextClient.getDailyUsage(nextPrefix, usageDate)
          : Promise.resolve([]),
      ])
      setRuns(nextRuns)
      setUsage(nextUsage)
    } catch (caught) {
      setError(caught instanceof AdminApiError ? caught.message : '管理员数据加载失败。')
    } finally {
      setLoading(false)
    }
  }

  const login = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalized = adminKey.trim()
    if (!normalized) {
      setError('请输入管理员 Key。')
      return
    }
    const nextClient = createAdminClient({ apiBaseUrl, apiKey: normalized })
    setLoading(true)
    setError(null)
    try {
      const nextKeys = await nextClient.listKeys()
      sessionStorage.setItem(ADMIN_KEY_STORAGE, normalized)
      setClient(nextClient)
      setNotice('管理员登录成功。')
      await loadDashboard(nextClient, nextKeys)
    } catch (caught) {
      setClient(null)
      setError(caught instanceof AdminApiError ? caught.message : '管理员登录失败。')
      sessionStorage.removeItem(ADMIN_KEY_STORAGE)
      setLoading(false)
    }
  }

  const logout = () => {
    sessionStorage.removeItem(ADMIN_KEY_STORAGE)
    setClient(null)
    setCreatedKey(null)
    setNotice('已退出管理员后台。')
  }

  const createKey = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!client || !newKeyName.trim()) return
    setLoading(true)
    setError(null)
    try {
      const result = await client.createKey(newKeyName.trim())
      setCreatedKey(result)
      setNotice('普通用户 Key 已创建；原始 Key 只在这里显示一次。')
      await loadDashboard(client)
    } catch (caught) {
      setError(caught instanceof AdminApiError ? caught.message : '创建普通 Key 失败。')
      setLoading(false)
    }
  }

  const revokeKey = async (prefix: string) => {
    if (!client || !window.confirm(`确定撤销 Key ${prefix} 吗？撤销后用户将无法继续调用模型。`))
      return
    setLoading(true)
    setError(null)
    try {
      await client.revokeKey(prefix)
      setNotice(`Key ${prefix} 已撤销。`)
      await loadDashboard(client)
    } catch (caught) {
      setError(caught instanceof AdminApiError ? caught.message : '撤销 Key 失败。')
      setLoading(false)
    }
  }

  const refreshUsage = async ({
    prefix = selectedPrefix,
    period = usagePeriod,
    date = usageDate,
    month = usageMonth,
  }: {
    prefix?: string
    period?: UsagePeriod
    date?: string
    month?: string
  } = {}) => {
    if (!client || !prefix) return
    setLoading(true)
    setError(null)
    try {
      const nextUsage =
        period === 'monthly'
          ? await client.getMonthlyUsage(prefix, month)
          : await client.getDailyUsage(prefix, date)
      setUsage(nextUsage)
    } catch (caught) {
      setError(caught instanceof AdminApiError ? caught.message : 'Token 用量加载失败。')
    } finally {
      setLoading(false)
    }
  }

  const copyCreatedKey = async () => {
    if (!createdKey) return
    try {
      await navigator.clipboard.writeText(createdKey.raw_key)
      setNotice('原始 Key 已复制，请立即交给用户。')
    } catch {
      setError('复制失败，请手动选择并复制原始 Key。')
    }
  }

  const loadQuota = async (): Promise<void> => {
    if (!client || !quotaWorkspaceId.trim()) {
      setQuotaError('请输入 Workspace ID。')
      return
    }
    setQuotaError(null)
    setQuotaNotice(null)
    try {
      const quota = await client.getWorkspaceQuota(quotaWorkspaceId.trim())
      setQuotaDaily(quota.daily_token_limit === null ? '' : String(quota.daily_token_limit))
      setQuotaMonthly(
        quota.monthly_token_limit === null ? '' : String(quota.monthly_token_limit),
      )
      setQuotaNotice('已加载当前配额设置。')
    } catch (caught) {
      setQuotaError(caught instanceof Error ? caught.message : '配额读取失败。')
    }
  }

  const loadAuditEvents = async (): Promise<void> => {
    if (!client) return
    setAuditError(null)
    try {
      setAuditEvents(await client.listAuditEvents({ limit: 50 }))
    } catch (caught) {
      setAuditError(caught instanceof Error ? caught.message : '审计记录加载失败。')
      setAuditEvents(null)
    }
  }

  const saveQuota = async (): Promise<void> => {
    if (!client || !quotaWorkspaceId.trim()) {
      setQuotaError('请输入 Workspace ID。')
      return
    }
    setQuotaError(null)
    setQuotaNotice(null)
    try {
      await client.setWorkspaceQuota(quotaWorkspaceId.trim(), {
        daily_token_limit: quotaDaily.trim() === '' ? null : Number(quotaDaily),
        monthly_token_limit: quotaMonthly.trim() === '' ? null : Number(quotaMonthly),
      })
      setQuotaNotice('配额已保存（空值 = 继承全局默认）。')
    } catch (caught) {
      setQuotaError(caught instanceof Error ? caught.message : '配额保存失败。')
    }
  }

  const showRun = async (runId: string) => {
    if (!client) return
    setLoading(true)
    setError(null)
    try {
      setSelectedRun(await client.getRun(runId))
    } catch (caught) {
      setError(caught instanceof AdminApiError ? caught.message : 'Agent Run 详情加载失败。')
    } finally {
      setLoading(false)
    }
  }

  if (!client) {
    return (
      <main className="shell adminShell">
        <section className="hero adminHero">
          <div>
            <p className="eyebrow">Admin Console · Access Control</p>
            <h1>管理员后台登录</h1>
            <p className="heroCopy">
              管理员 Key 只用于管理普通用户 Key、Token 用量和审计记录，不要交给普通用户。
            </p>
          </div>
          <button type="button" className="secondaryButton" onClick={onBack}>
            返回模型控制台
          </button>
        </section>
        <section className="panel adminLoginPanel">
          <form onSubmit={login} className="adminForm">
            <label htmlFor="admin-key">管理员 Key</label>
            <input
              id="admin-key"
              type="password"
              value={adminKey}
              onChange={(event) => setAdminKey(event.target.value)}
              placeholder="输入管理员 Key"
              autoComplete="off"
            />
            <p className="formHint">登录后不会展示管理员 Key；浏览器只在当前会话保存它。</p>
            {error ? (
              <div className="errorNotice" role="alert">
                {error}
              </div>
            ) : null}
            <button type="submit" disabled={loading}>
              {loading ? '登录中…' : '登录管理员后台'}
            </button>
          </form>
        </section>
      </main>
    )
  }

  return (
    <main className="shell adminShell">
      <section className="hero adminHero">
        <div>
          <p className="eyebrow">Admin Console · Access Control</p>
          <h1>管理员后台</h1>
          <p className="heroCopy">
            创建普通用户 Key，查看状态和用量，并审计 Agent Run、工具调用与 RAG 来源。
          </p>
        </div>
        <div className="heroActions">
          <span className="statusPill">已登录</span>
          <button type="button" className="secondaryButton" onClick={onBack}>
            模型控制台
          </button>
          <button type="button" className="secondaryButton" onClick={logout}>
            退出登录
          </button>
        </div>
      </section>

      {notice ? (
        <div className="successNotice" role="status">
          {notice}
        </div>
      ) : null}
      {error ? (
        <div className="errorNotice" role="alert">
          {error}
        </div>
      ) : null}

      <section className="adminGrid">
        <article className="panel adminCard">
          <div className="panelHeader">
            <div>
              <h2>创建普通用户 Key</h2>
              <span>原始 Key 只显示一次</span>
            </div>
          </div>
          <form onSubmit={createKey} className="adminForm compactForm">
            <label htmlFor="new-key-name">用户或用途名称</label>
            <input
              id="new-key-name"
              value={newKeyName}
              onChange={(event) => setNewKeyName(event.target.value)}
            />
            <button type="submit" disabled={loading || !newKeyName.trim()}>
              创建普通 API Key
            </button>
          </form>
          {createdKey ? (
            <div className="secretKeyNotice">
              <strong>请立即复制并交给用户</strong>
              <p>关闭或刷新页面后，原始 Key 不会再次返回。</p>
              <code>{createdKey.raw_key}</code>
              <button type="button" onClick={() => void copyCreatedKey()}>
                复制原始 Key
              </button>
            </div>
          ) : null}
        </article>

        <article className="panel adminCard">
          <div className="panelHeader">
            <div>
              <h2>Token 用量</h2>
              <span>按用户 Key 查看</span>
            </div>
          </div>
          <div className="adminControls">
            <div className="usagePeriodSwitch" role="group" aria-label="Token 用量周期">
              <button
                type="button"
                className={usagePeriod === 'daily' ? 'modeActive' : 'secondaryButton'}
                aria-pressed={usagePeriod === 'daily'}
                onClick={() => {
                  setUsagePeriod('daily')
                  void refreshUsage({ period: 'daily' })
                }}
              >
                按日
              </button>
              <button
                type="button"
                className={usagePeriod === 'monthly' ? 'modeActive' : 'secondaryButton'}
                aria-pressed={usagePeriod === 'monthly'}
                onClick={() => {
                  setUsagePeriod('monthly')
                  void refreshUsage({ period: 'monthly' })
                }}
              >
                按月
              </button>
            </div>
            <label htmlFor="usage-key">用户 Key</label>
            <select
              id="usage-key"
              value={selectedPrefix}
              onChange={(event) => {
                setSelectedPrefix(event.target.value)
                void refreshUsage({ prefix: event.target.value })
              }}
            >
              <option value="">选择 Key</option>
              {ordinaryKeys.map((item) => (
                <option key={item.key_hash_prefix} value={item.key_hash_prefix}>
                  {item.name} · {item.key_hash_prefix}
                </option>
              ))}
            </select>
            {usagePeriod === 'daily' ? (
              <>
                <label htmlFor="usage-date">北京时间日期</label>
                <input
                  id="usage-date"
                  type="date"
                  value={usageDate}
                  onChange={(event) => {
                    setUsageDate(event.target.value)
                    void refreshUsage({ date: event.target.value, period: 'daily' })
                  }}
                />
              </>
            ) : (
              <>
                <label htmlFor="usage-month">北京时间月份</label>
                <input
                  id="usage-month"
                  type="month"
                  value={usageMonth}
                  onChange={(event) => {
                    setUsageMonth(event.target.value)
                    void refreshUsage({ month: event.target.value, period: 'monthly' })
                  }}
                />
              </>
            )}
          </div>
          <div className="usageTotals">
            <div>
              <span>请求数</span>
              <strong>{formatNumber(totals.requests)}</strong>
            </div>
            <div>
              <span>Prompt Token</span>
              <strong>{formatNumber(totals.prompt)}</strong>
            </div>
            <div>
              <span>Completion Token</span>
              <strong>{formatNumber(totals.completion)}</strong>
            </div>
            <div>
              <span>总 Token</span>
              <strong>{formatNumber(totals.total)}</strong>
            </div>
          </div>
          <div className="usageTable" role="table" aria-label="Token 用量明细">
            <div className="usageRow usageHeader">
              <span>模型</span>
              <span>请求</span>
              <span>总 Token</span>
            </div>
            {usage.map((item) => (
              <div className="usageRow" key={item.model}>
                <span>{item.model}</span>
                <span>{formatNumber(item.request_count)}</span>
                <span>{formatNumber(item.total_tokens)}</span>
              </div>
            ))}
            {usage.length === 0 ? (
              <p className="formHint">
                当前{usagePeriod === 'daily' ? '日期' : '月份'}暂无用量记录。
              </p>
            ) : null}
          </div>
          <p className="formHint">
            仅展示普通用户 Key；日期和月份均按 Asia/Shanghai（北京时间）查询。
          </p>
        </article>
      </section>

      <section className="panel adminCard">
        <div className="panelHeader">
          <div>
            <h2>普通 API Key 管理</h2>
            <span>管理员可以查看状态或撤销</span>
          </div>
          <button
            type="button"
            className="secondaryButton"
            onClick={() => void loadDashboard(client)}
            disabled={loading}
          >
            刷新
          </button>
        </div>
        <div className="keyTable" role="table" aria-label="API Key 列表">
          <div className="keyRow keyHeader">
            <span>名称</span>
            <span>前缀</span>
            <span>状态</span>
            <span>创建时间（上海）</span>
            <span>操作</span>
          </div>
          {ordinaryKeys.map((item) => (
            <div className="keyRow" key={item.key_hash_prefix}>
              <span>{item.name}</span>
              <code>{item.key_hash_prefix}…</code>
              <span className={`statusText status-${item.status}`}>
                {formatStatus(item.status)}
              </span>
              <span>{formatAgentTimestamp(item.created_at)}</span>
              <span>
                {item.status === 'active' ? (
                  <button
                    type="button"
                    className="dangerButton"
                    onClick={() => void revokeKey(item.key_hash_prefix)}
                  >
                    撤销
                  </button>
                ) : (
                  '—'
                )}
              </span>
            </div>
          ))}
          {ordinaryKeys.length === 0 ? <p className="formHint">暂无普通 Key。</p> : null}
        </div>
      </section>

      <section className="panel adminCard">
        <div className="panelHeader">
          <div>
            <h2>Agent Run / RAG 审计记录</h2>
            <span>不保存原始用户 Key；仅展示安全摘要和 RAG 来源</span>
          </div>
          <button
            type="button"
            className="secondaryButton"
            onClick={() => void loadDashboard(client)}
            disabled={loading}
          >
            刷新记录
          </button>
        </div>
        <div className="runTable" role="table" aria-label="Agent Run 记录">
          <div className="runRow runHeader">
            <span>时间（上海）</span>
            <span>用户</span>
            <span>模型</span>
            <span>状态</span>
            <span>Token</span>
            <span>工具 / RAG</span>
          </div>
          {runs.map((run) => (
            <button
              type="button"
              className="runRow runButton"
              key={run.run_id}
              onClick={() => void showRun(run.run_id)}
            >
              <span>{formatAgentTimestamp(run.started_at)}</span>
              <span>{run.api_key_name}</span>
              <span>{run.model}</span>
              <span>{formatStatus(run.status)}</span>
              <span>{formatNumber(run.total_tokens)}</span>
              <span>
                {run.tool_count} / {run.rag_reference_count}
              </span>
            </button>
          ))}
          {runs.length === 0 ? (
            <p className="formHint">暂无 Agent Run 记录；完成一次 Agent 调用后会出现在这里。</p>
          ) : null}
        </div>
      </section>

      <section className="panel adminCard quotaCard">
        <div className="panelHeader">
          <div>
            <h2>Workspace 配额</h2>
            <p>设置 workspace 级每日/每月 Token 限额；空值 = 继承全局默认（QUOTA_SCOPE=workspace 时生效）。</p>
          </div>
        </div>
        <div className="quotaForm">
          <input
            aria-label="Workspace ID"
            placeholder="Workspace ID"
            value={quotaWorkspaceId}
            onChange={(event) => setQuotaWorkspaceId(event.target.value)}
          />
          <input
            aria-label="每日限额"
            placeholder="每日限额 (token)"
            type="number"
            min={0}
            value={quotaDaily}
            onChange={(event) => setQuotaDaily(event.target.value)}
          />
          <input
            aria-label="每月限额"
            placeholder="每月限额 (token)"
            type="number"
            min={0}
            value={quotaMonthly}
            onChange={(event) => setQuotaMonthly(event.target.value)}
          />
          <button type="button" onClick={() => void loadQuota()}>
            读取
          </button>
          <button type="button" onClick={() => void saveQuota()}>
            保存
          </button>
        </div>
        {quotaError !== null && <p className="inlineError" role="alert">{quotaError}</p>}
        {quotaNotice !== null && <p className="inlineNotice" role="status">{quotaNotice}</p>}
      </section>

      <section className="panel adminCard auditCard">
        <div className="panelHeader">
          <div>
            <h2>审计记录</h2>
            <p>关键操作（agent/prompt/tool/benchmark/成员）的 before/after 快照。</p>
          </div>
          <button type="button" onClick={() => void loadAuditEvents()}>
            刷新
          </button>
        </div>
        {auditError !== null && (
          <p className="inlineError" role="alert">{auditError}</p>
        )}
        {auditEvents !== null && auditEvents.length === 0 && (
          <p className="formHint">暂无审计记录。</p>
        )}
        {auditEvents !== null && auditEvents.length > 0 && (
          <table className="auditTable">
            <thead>
              <tr>
                <th>时间</th>
                <th>操作</th>
                <th>资源</th>
                <th>actor</th>
                <th>变更</th>
              </tr>
            </thead>
            <tbody>
              {auditEvents.map((event) => (
                <tr key={event.id}>
                  <td>
                    {event.created_at === null
                      ? '--'
                      : new Date(event.created_at).toLocaleString('zh-CN', {
                          hour12: false,
                        })}
                  </td>
                  <td>{event.action}</td>
                  <td>
                    {event.resource_type}/{event.resource_id}
                  </td>
                  <td>{event.user_id ?? event.api_key_hash?.slice(0, 8) ?? '--'}</td>
                  <td>
                    {event.after !== null
                      ? JSON.stringify(event.after).slice(0, 80)
                      : '--'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {selectedRun ? (
        <section className="panel adminCard runDetail">
          <div className="panelHeader">
            <div>
              <h2>Run 详情</h2>
              <span>{selectedRun.run_id}</span>
            </div>
            <button type="button" className="secondaryButton" onClick={() => setSelectedRun(null)}>
              关闭
            </button>
          </div>
          <div className="traceMeta">
            <span>用户：{selectedRun.api_key_name}</span>
            <span>模型：{selectedRun.model}</span>
            <span>状态：{formatStatus(selectedRun.status)}</span>
            <span>Token：{formatNumber(selectedRun.total_tokens)}</span>
            <span>
              耗时：{selectedRun.duration_ms === null ? '—' : `${selectedRun.duration_ms} ms`}
            </span>
          </div>
          <h3>安全摘要</h3>
          <p className="adminAnswer">{runDetails(selectedRun)}</p>
          <details>
            <summary>查看步骤、工具调用与 RAG 来源 JSON</summary>
            <pre className="recordJson">{JSON.stringify(selectedRun.response, null, 2)}</pre>
          </details>
        </section>
      ) : null}

      <footer className="metricsBar">
        <div>
          <span className="metricLabel">Key 数量</span>
          <strong>{keys.length}</strong>
        </div>
        <div>
          <span className="metricLabel">Agent Run</span>
          <strong>{runs.length}</strong>
        </div>
        <div className="metricWide">
          <span className="metricLabel">安全提示</span>
          <strong>管理员 Key 不交给普通用户</strong>
        </div>
      </footer>
    </main>
  )
}

export { USER_KEY_STORAGE }
