import { strict as assert } from 'node:assert'
import { once } from 'node:events'
import { existsSync } from 'node:fs'
import { createServer } from 'node:net'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { setTimeout as delay } from 'node:timers/promises'
import { chromium } from 'playwright'
import axe from 'axe-core'

const frontendDir = fileURLToPath(new URL('..', import.meta.url))
const waitTimeoutMs = Number(process.env.A11Y_SMOKE_TIMEOUT_MS ?? 60_000)
const skipAgent = process.env.A11Y_SMOKE_SKIP_AGENT === '1'

const getFreePort = async () => {
  const server = createServer()
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const address = server.address()
  assert(address && typeof address === 'object', '无法获取浏览器 smoke 端口。')
  const port = address.port
  await new Promise((resolve, reject) => {
    server.close((error) => (error ? reject(error) : resolve()))
  })
  return port
}

const waitForHttp = async (url, timeoutMs = 15_000) => {
  const deadline = Date.now() + timeoutMs
  let lastError = null
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return
      lastError = new Error(`HTTP ${response.status}`)
    } catch (error) {
      lastError = error
    }
    await delay(200)
  }
  throw new Error(
    `等待 ${url} 超时：${lastError instanceof Error ? lastError.message : '未知错误'}`,
  )
}

const startVite = async () => {
  const port = await getFreePort()
  const npmCommand = process.platform === 'win32' ? 'npm.cmd' : 'npm'
  const output = []
  const vite = spawn(
    npmCommand,
    ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    {
      cwd: frontendDir,
      env: process.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  )
  vite.stdout.on('data', (chunk) => output.push(String(chunk)))
  vite.stderr.on('data', (chunk) => output.push(String(chunk)))
  const url = `http://127.0.0.1:${port}/`
  try {
    await waitForHttp(url)
  } catch (error) {
    vite.kill('SIGTERM')
    throw new Error(
      `${error instanceof Error ? error.message : String(error)}\nVite 输出：${output.join('')}`,
    )
  }
  return {
    url,
    stop: async () => {
      if (vite.exitCode !== null) return
      vite.kill('SIGTERM')
      await Promise.race([once(vite, 'exit'), delay(3_000)])
      if (vite.exitCode === null) vite.kill('SIGKILL')
    },
  }
}

const resolveBrowserExecutable = () => {
  const candidates = [
    process.env.A11Y_SMOKE_BROWSER_PATH,
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    chromium.executablePath(),
  ].filter(Boolean)
  return candidates.find((candidate) => existsSync(candidate)) ?? null
}

const formatAxeFindings = (findings) =>
  findings
    .map(
      (finding) =>
        `${finding.id} (${finding.impact ?? 'unknown'}): ${finding.nodes
          .map((node) => `${node.target.join(', ')} — ${node.failureSummary ?? node.html}`)
          .join(' | ')}`,
    )
    .join('\n')

const runAxe = async (page, label, options = {}) => {
  const result = await page.evaluate(async (axeOptions) => {
    const axeResult = await window.axe.run(
      axeOptions.include ? { include: axeOptions.include } : document,
      {
        resultTypes: ['violations', 'incomplete'],
      },
    )
    return {
      violations: axeResult.violations.map(({ id, impact, nodes }) => ({
        id,
        impact,
        nodes: nodes.map(({ target, html, failureSummary }) => ({
          target,
          html,
          failureSummary,
        })),
      })),
      incomplete: axeResult.incomplete.map(({ id, impact, nodes }) => ({
        id,
        impact,
        nodes: nodes.map(({ target, html, failureSummary }) => ({
          target,
          html,
          failureSummary,
        })),
      })),
    }
  }, options)
  console.log(
    `[axe] ${label}: violations=${result.violations.length}, incomplete=${result.incomplete.length}`,
  )
  if (result.incomplete.length > 0) {
    console.log(
      `[axe] ${label} incomplete（按当前 DOM 如实记录）：\n${formatAxeFindings(result.incomplete)}`,
    )
  }
  assert.equal(
    result.violations.length,
    0,
    `[axe] ${label} 存在未豁免 violation：\n${formatAxeFindings(result.violations)}`,
  )
  return result
}

const assertFocused = async (locator, label) => {
  await locator.focus()
  assert.equal(
    await locator.evaluate((element) => element === document.activeElement),
    true,
    `${label} 不可聚焦。`,
  )
}

const assertDisclosureRelation = async (button, label) => {
  const controlsId = await button.getAttribute('aria-controls')
  assert(controlsId, `${label} 缺少 aria-controls。`)
  const content = button.locator(`xpath=following::*[@id="${controlsId}"][1]`).first()
  assert.equal(await content.count(), 1, `${label} 的 aria-controls 未指向唯一 DOM 节点。`)
  const expanded = await button.getAttribute('aria-expanded')
  assert(expanded === 'true' || expanded === 'false', `${label} 的 aria-expanded 不是布尔字符串。`)
  const hidden = (await content.getAttribute('hidden')) !== null
  assert.equal(hidden, expanded !== 'true', `${label} 的 hidden 与 aria-expanded 不一致。`)
  return { content, expanded: expanded === 'true' }
}

const assertAllDisclosureRelations = async (page) => {
  const buttons = page.locator('button[aria-expanded][aria-controls]')
  const count = await buttons.count()
  assert(count > 0, '未找到任何 disclosure 按钮。')
  for (let index = 0; index < count; index += 1) {
    await assertDisclosureRelation(buttons.nth(index), `disclosure #${index + 1}`)
  }
  console.log(`[dom] 已检查 ${count} 个 disclosure 的 aria-expanded/aria-controls/hidden 关系。`)
}

const exerciseDisclosure = async (button, label) => {
  const initial = await assertDisclosureRelation(button, label)
  await assertFocused(button, label)
  await button.press('Space')
  const toggled = await assertDisclosureRelation(button, `${label}（键盘切换后）`)
  assert.notEqual(toggled.expanded, initial.expanded, `${label} 未响应 Space 键。`)
  assert.equal(
    await button.evaluate((element) => element === document.activeElement),
    true,
    `${label} 切换后焦点丢失。`,
  )
  await button.press('Space')
  const restored = await assertDisclosureRelation(button, `${label}（恢复后）`)
  assert.equal(restored.expanded, initial.expanded, `${label} 无法恢复原始状态。`)
}

const attachLiveRegionObserver = async (page) => {
  await page.evaluate(() => {
    const liveRegion = document.querySelector('.srOnlyStatus')
    window.__a11ySmokeLiveValues = []
    if (!liveRegion) return
    const record = () => {
      window.__a11ySmokeLiveValues.push(liveRegion.textContent?.trim() ?? '')
    }
    record()
    window.__a11ySmokeLiveObserver = new MutationObserver(record)
    window.__a11ySmokeLiveObserver.observe(liveRegion, {
      subtree: true,
      childList: true,
      characterData: true,
    })
  })
}

const stopLiveRegionObserver = async (page) =>
  page.evaluate(() => {
    window.__a11ySmokeLiveObserver?.disconnect()
    return window.__a11ySmokeLiveValues ?? []
  })

const assertPlatformOverview = async (page) => {
  assert.equal(await page.title(), 'Agent Console | AI Platform Mini', '页面标题不正确。')
  await page
    .getByRole('heading', { name: '把模型能力，变成可观察的应用。', exact: true })
    .waitFor({ state: 'visible' })
  await page
    .getByText(
      'AI Platform Mini 是一个轻量级大模型应用平台：从流式对话到 Agent Runtime，再到 RAG 来源审计，所有演示都连接真实的后端能力。',
      { exact: true },
    )
    .waitFor({ state: 'visible' })
  const overviewNav = page.getByRole('button', { name: '平台概览', exact: true })
  assert.equal(
    await overviewNav.getAttribute('aria-current'),
    'page',
    '平台概览入口未标记为当前页面。',
  )
  assert.equal(
    await page.locator('[aria-label="平台能力状态"]').count(),
    1,
    '平台能力状态区域未渲染。',
  )
  await page.getByRole('heading', { name: '从一个入口，讲清楚四种 AI 能力', exact: true }).waitFor({
    state: 'visible',
  })
  await page
    .getByRole('button', { name: '打开对话工作台', exact: true })
    .waitFor({ state: 'visible' })
  await page
    .getByRole('button', { name: '运行 Agent Demo', exact: true })
    .waitFor({ state: 'visible' })
  await page
    .getByRole('button', { name: '进入知识库演示', exact: true })
    .waitFor({ state: 'visible' })
  await page
    .getByRole('button', { name: '打开 Prompt Studio', exact: true })
    .waitFor({ state: 'visible' })
  console.log('[dom] 平台概览入口、能力状态和四条演示路径检查通过。')
}

const openConsoleFromOverview = async (page) => {
  await page.getByRole('button', { name: '打开对话工作台', exact: true }).click()
  await page.getByRole('heading', { name: '对话与 Agent Trace', exact: true }).waitFor({
    state: 'visible',
  })
  const consoleNav = page.getByRole('button', { name: '对话工作台', exact: true })
  assert.equal(
    await consoleNav.getAttribute('aria-current'),
    'page',
    '进入对话工作台后导航状态未更新。',
  )
}

const assertConsoleInitialState = async (page) => {
  await page.getByText('开始一段普通对话', { exact: true }).waitFor({ state: 'visible' })
  await page
    .getByText('输入问题后，前端会真实调用 Chat SSE，并将回答增量显示在这里。', { exact: true })
    .waitFor({ state: 'visible' })
  const modeGroup = page.getByRole('group', { name: '请求模式', exact: true })
  assert.equal(await modeGroup.count(), 1, '请求模式未暴露为可命名的 group。')
  const chatMode = modeGroup.getByRole('button', { name: '普通 Chat SSE 模式', exact: true })
  const agentMode = modeGroup.getByRole('button', { name: 'Agent Run 模式', exact: true })
  assert.equal(
    await chatMode.getAttribute('aria-pressed'),
    'true',
    '初始 Chat 模式未标记为 pressed。',
  )
  assert.equal(
    await agentMode.getAttribute('aria-pressed'),
    'false',
    '初始 Agent 模式错误标记为 pressed。',
  )
  const input = page.getByRole('textbox', { name: '输入消息', exact: true })
  assert.equal(await input.isEnabled(), true, '输入框初始不可用。')
  const send = page.getByRole('button', { name: '发送消息', exact: true })
  assert.equal(await send.isDisabled(), true, '空态发送按钮应保持 disabled。')
  await assertFocused(input, '输入框')
  await assertFocused(chatMode, 'Chat 模式按钮')
  await assertFocused(agentMode, 'Agent 模式按钮')
  await assertFocused(page.getByRole('button', { name: '新建会话', exact: true }), '新建会话按钮')
  await assertFocused(
    page.getByRole('button', { name: '清空当前会话', exact: true }),
    '清空会话按钮',
  )
  await input.fill('可访问性 smoke')
  assert.equal(await send.isEnabled(), true, '非空输入未启用发送按钮。')
  await assertFocused(send, '发送按钮')
  await input.fill('')
  console.log('[dom] 对话工作台空态、请求模式语义、输入框和主要按钮焦点检查通过。')
}

const switchToAgentMode = async (page) => {
  const agentMode = page.getByRole('button', { name: 'Agent Run 模式', exact: true })
  await agentMode.click()
  assert.equal(
    await agentMode.getAttribute('aria-pressed'),
    'true',
    'Agent 模式切换未更新 aria-pressed。',
  )
  assert.equal(
    await page
      .getByRole('button', { name: '普通 Chat SSE 模式', exact: true })
      .getAttribute('aria-pressed'),
    'false',
    'Chat 模式切换后仍标记为 pressed。',
  )
  await page.getByText('运行一次真实 Agent', { exact: true }).waitFor({ state: 'visible' })
  console.log('[dom] 请求模式切换语义检查通过。')
}

const runRealAgentScenario = async (page) => {
  const input = page.getByRole('textbox', { name: '输入消息', exact: true })
  const runButton = page.getByRole('button', { name: '运行 Agent', exact: true })
  const message =
    process.env.A11Y_SMOKE_AGENT_MESSAGE ??
    '请先调用 knowledge_search 查询“阶段六前端可访问性”，再根据检索结果回答；不要直接回答，也不要调用其他工具。'
  await attachLiveRegionObserver(page)
  await input.fill(message)
  await runButton.click()
  await page.locator('.stepTimeline button[aria-controls]').first().waitFor({
    state: 'visible',
    timeout: waitTimeoutMs,
  })
  await page.getByRole('button', { name: '运行 Agent', exact: true }).waitFor({
    state: 'visible',
    timeout: waitTimeoutMs,
  })

  await assertAllDisclosureRelations(page)
  const traceButton = page.locator('.stepTimeline > li > button.traceToggle').first()
  await exerciseDisclosure(traceButton, 'Trace 步骤 disclosure')

  const toolButton = page.getByRole('button', { name: /knowledge_search/ }).first()
  assert.equal(
    await toolButton.count(),
    1,
    '真实 Agent Run 未产生 knowledge_search Tool Call，无法检查 RAG disclosure。',
  )
  if ((await toolButton.getAttribute('aria-expanded')) !== 'true') await toolButton.click()
  const ragButton = page.locator('.ragToggle').first()
  await ragButton.waitFor({ state: 'visible', timeout: 10_000 })
  await exerciseDisclosure(ragButton, 'RAG disclosure')
  await assertAllDisclosureRelations(page)

  const liveValues = await stopLiveRegionObserver(page)
  const liveRegion = page.locator('.srOnlyStatus')
  assert.equal(
    await liveRegion.getAttribute('role'),
    'status',
    '运行状态 live region 缺少 role=status。',
  )
  assert.equal(
    await liveRegion.getAttribute('aria-live'),
    'polite',
    '运行状态 live region 必须使用 polite。',
  )
  assert.equal(
    await liveRegion.getAttribute('aria-atomic'),
    'true',
    '运行状态 live region 必须使用 atomic=true。',
  )
  assert(liveValues.length > 1, '运行过程中没有可观察的 live region 状态变化。')
  assert(new Set(liveValues).size > 1, '运行状态 live region 没有发生状态变化。')
  const assistantAnswer = await page.locator('.message-assistant p').last().innerText()
  assert(assistantAnswer.length > 1, '真实 Agent Run 未产生可用于播报隔离检查的回答文本。')
  assert(
    liveValues.every((value) => !value.includes(assistantAnswer)),
    'live region 包含完整回答文本，疑似逐字/逐回答播报。',
  )
  assert.equal(
    await liveRegion.locator('.message-assistant').count(),
    0,
    '回答 DOM 不应位于运行状态 live region 内。',
  )
  console.log(
    `[dom] Trace/RAG disclosure、焦点保持和 live region 非逐字播报检查通过（${liveValues.length} 次状态变更）。`,
  )
}

const assertNoHorizontalOverflow = async (page) => {
  await page.setViewportSize({ width: 320, height: 900 })
  await page.waitForTimeout(100)
  const dimensions = await page.evaluate(() => {
    const viewportWidth = document.documentElement.clientWidth
    const visibleOverflowingElements = [...document.querySelectorAll('*')]
      .filter((element) => {
        const style = getComputedStyle(element)
        return (
          style.display !== 'none' &&
          style.visibility !== 'hidden' &&
          !element.closest('[hidden]') &&
          !element.closest('.platformNav')
        )
      })
      .map((element) => {
        const rect = element.getBoundingClientRect()
        return { tag: element.tagName, className: element.className, right: rect.right }
      })
      .filter(({ right }) => right > viewportWidth + 1)
      .slice(0, 10)
    return {
      viewportWidth,
      documentWidth: document.documentElement.scrollWidth,
      bodyWidth: document.body.scrollWidth,
      visibleOverflowingElements,
    }
  })
  assert(
    dimensions.documentWidth <= dimensions.viewportWidth + 1,
    `窄视口 document 横向溢出：${JSON.stringify(dimensions)}`,
  )
  assert(
    dimensions.bodyWidth <= dimensions.viewportWidth + 1,
    `窄视口 body 横向溢出：${JSON.stringify(dimensions)}`,
  )
  assert.equal(
    dimensions.visibleOverflowingElements.length,
    0,
    `存在可见横向溢出节点：${JSON.stringify(dimensions)}`,
  )
  console.log('[layout] 320px 窄视口无横向溢出。')
}

const main = async () => {
  let vite = null
  let browser = null
  try {
    const externalUrl = process.env.A11Y_SMOKE_URL?.trim()
    if (externalUrl) {
      await waitForHttp(externalUrl)
    } else {
      vite = await startVite()
    }
    const appUrl = externalUrl || vite.url
    const executablePath = resolveBrowserExecutable()
    if (!executablePath) {
      throw new Error(
        '未找到 Chrome/Chromium。可设置 A11Y_SMOKE_BROWSER_PATH，或执行 npx playwright install chromium。',
      )
    }
    browser = await chromium.launch({
      executablePath,
      headless: process.env.A11Y_SMOKE_HEADED !== '1',
    })
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } })
    page.setDefaultTimeout(waitTimeoutMs)
    const consoleErrors = []
    const pageErrors = []
    page.on('console', (message) => {
      if (message.type() === 'error') consoleErrors.push(message.text())
    })
    page.on('pageerror', (error) => pageErrors.push(error.message))

    await page.goto(appUrl, { waitUntil: 'networkidle', timeout: waitTimeoutMs })
    await page.addScriptTag({ content: axe.source })
    await page.locator('#root').waitFor({ state: 'visible' })
    await assertPlatformOverview(page)
    await openConsoleFromOverview(page)
    await assertConsoleInitialState(page)
    await runAxe(page, '对话工作台初始空态', { include: ['.consolePage'] })
    await switchToAgentMode(page)
    if (skipAgent) {
      console.log('[skip] A11Y_SMOKE_SKIP_AGENT=1，未执行真实 Agent/Trace/RAG 路径。')
    } else {
      await runRealAgentScenario(page)
      await runAxe(page, '真实 Agent/RAG 状态', { include: ['.consolePage'] })
    }
    await assertNoHorizontalOverflow(page)
    assert.equal(consoleErrors.length, 0, `浏览器 console error：${consoleErrors.join(' | ')}`)
    assert.equal(pageErrors.length, 0, `浏览器 page error：${pageErrors.join(' | ')}`)
    console.log('A11Y smoke 通过。真实屏幕阅读器验收不在本脚本范围内。')
  } finally {
    await browser?.close()
    await vite?.stop()
  }
}

try {
  await main()
} catch (error) {
  console.error(`A11Y smoke 失败：${error instanceof Error ? error.stack : String(error)}`)
  process.exitCode = 1
}
