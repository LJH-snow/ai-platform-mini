import type { JSX } from 'react'
import { useMemo, useState } from 'react'
import './App.css'

type ConsoleState = {
  sessionCount: number
  lastAction: string
  clearedCount: number
}

function App(): JSX.Element {
  const [consoleState, setConsoleState] = useState<ConsoleState>({
    sessionCount: 0,
    lastAction: '尚未创建会话',
    clearedCount: 0,
  })

  const sessionLabel = useMemo(() => {
    if (consoleState.sessionCount === 0) {
      return '无本地会话'
    }

    return `本地会话 ${consoleState.sessionCount}`
  }, [consoleState.sessionCount])

  const handleCreateSession = (): void => {
    setConsoleState((currentState) => ({
      ...currentState,
      sessionCount: currentState.sessionCount + 1,
      lastAction: '已创建一个本地空会话',
    }))
  }

  const handleClear = (): void => {
    setConsoleState((currentState) => ({
      sessionCount: 0,
      lastAction: '已清空本地控制台状态',
      clearedCount: currentState.clearedCount + 1,
    }))
  }

  return (
    <main className="shell">
      <section className="hero">
        <div>
          <p className="eyebrow">Agent Console · Phase 1</p>
          <h1>本地前端骨架</h1>
          <p className="heroCopy">
            React + TypeScript + Vite 控制台空状态已就绪。阶段 1 暂未接入后端 SSE， 因此不会伪造
            Agent 事件或 Trace 数据。
          </p>
        </div>
        <div className="statusPill">{sessionLabel}</div>
      </section>

      <section className="consoleGrid" aria-label="Agent Console empty state">
        <article className="panel conversationPanel">
          <div className="panelHeader">
            <h2>会话</h2>
            <span>Empty</span>
          </div>
          <div className="emptyState">
            <div className="emptyIcon">A</div>
            <h3>还没有 Agent 输出</h3>
            <p>点击“新建会话”只会更新本地界面状态，不会请求后端，也不会生成模拟事件。</p>
          </div>
        </article>

        <aside className="panel tracePanel">
          <div className="panelHeader">
            <h2>Trace</h2>
            <span>未接入</span>
          </div>
          <div className="traceNotice">
            <h3>SSE 尚未连接</h3>
            <p>
              阶段 1 仅展示布局与本地交互。Trace 流、步骤事件、Token
              统计会在后续阶段接入真实后端后显示。
            </p>
          </div>
          <ul className="traceList" aria-label="Trace placeholder list">
            <li>后端 SSE：未配置</li>
            <li>实时事件：不伪造</li>
            <li>执行轨迹：等待阶段 2</li>
          </ul>
        </aside>
      </section>

      <footer className="metricsBar">
        <div>
          <span className="metricLabel">会话数</span>
          <strong>{consoleState.sessionCount}</strong>
        </div>
        <div>
          <span className="metricLabel">清空次数</span>
          <strong>{consoleState.clearedCount}</strong>
        </div>
        <div className="metricWide">
          <span className="metricLabel">最近动作</span>
          <strong>{consoleState.lastAction}</strong>
        </div>
        <div className="actions">
          <button type="button" onClick={handleCreateSession}>
            新建会话
          </button>
          <button type="button" className="secondaryButton" onClick={handleClear}>
            清空
          </button>
        </div>
      </footer>
    </main>
  )
}

export default App
