import { expect, test, type Page } from '@playwright/test'

// Layering note: the RAG E2E asserts process completeness (preset forces
// knowledge_search, the tool tolerates an empty result under the mock
// embedder, and the mock LLM final-answers). Retrieval quality itself is
// covered by the Sprint C golden-set gate, not by this browser test.

/** Register a fresh user through the auth UI and land in the console. */
async function register(page: Page, email: string): Promise<void> {
  await page.goto('/')
  // Sidebar session entry opens the login page; switch to registration.
  await page.getByRole('button', { name: '登录 / 注册' }).click()
  await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()
  await page.getByRole('button', { name: '注册' }).click()
  await expect(page.getByRole('heading', { name: '注册' })).toBeVisible()
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('显示名称').fill(email.split('@')[0])
  await page.getByLabel('密码').fill('secret123')
  await page.getByRole('button', { name: '注册', exact: true }).click()
  // Registration auto-logs-in and lands on the platform shell.
  await expect(
    page.getByRole('button', { name: '对话工作台', exact: true }),
  ).toBeVisible({ timeout: 30_000 })
}

test('register → chat with the mock provider', async ({ page }) => {
  await register(page, `e2e-chat-${Date.now()}@test.com`)

  await page.getByRole('button', { name: '对话工作台', exact: true }).click()
  await page.getByLabel('输入消息').fill('你好')
  await page.getByRole('button', { name: '发送消息' }).click()

  await expect(page.getByText('Hello from Mock Provider')).toBeVisible({
    timeout: 30_000,
  })
})

test('agent run produces a trace and opens the replay page', async ({ page }) => {
  await register(page, `e2e-agent-${Date.now()}@test.com`)

  await page.getByRole('button', { name: '对话工作台', exact: true }).click()
  await page.getByRole('button', { name: 'Agent Run 模式' }).click()
  await page.getByLabel('输入消息').fill('用计算器算 2+2')
  await page.getByRole('button', { name: '运行 Agent' }).click()

  // Mock provider answers with the deterministic agent final answer.
  await expect(page.getByText('这是 Mock Provider 的最终回答。')).toBeVisible({
    timeout: 30_000,
  })
  // Trace exposes the Run ID and a replay button.
  const replay = page.getByRole('button', { name: /回放 Run/ })
  await expect(replay).toBeVisible({ timeout: 30_000 })
  await replay.click()

  // Replay timeline shows the stored run.
  await expect(page.getByRole('heading', { name: 'Agent Run 回放' })).toBeVisible()
  await expect(page.getByText('这是 Mock Provider 的最终回答。')).toBeVisible()
})

test('PDF workflow: upload → draft → approve → complete', async ({ page }) => {
  await register(page, `e2e-wf-${Date.now()}@test.com`)

  await page.getByRole('button', { name: 'PDF 工作流' }).click()
  await page.getByLabel('选择 PDF 文件').setInputFiles('e2e/fixtures/sample.pdf')
  await page.getByRole('button', { name: '开始生成报告' }).click()

  // Draft generation uses the mock LLM; poll until the report appears.
  await expect(page.getByText(/报告草稿已生成/)).toBeVisible({ timeout: 60_000 })

  await page.getByRole('button', { name: /批准生成/ }).click()
  await expect(page.getByText(/报告已生成/)).toBeVisible({
    timeout: 30_000,
  })
})

test('knowledge base: upload PDF, then ask via RAG agent preset', async ({
  page,
}) => {
  await register(page, `e2e-rag-${Date.now()}@test.com`)

  await page.getByRole('button', { name: '知识库', exact: true }).click()
  await page.getByLabel('选择 PDF 文件').setInputFiles('e2e/fixtures/sample.pdf')

  // Wait for ingestion to finish (success notice appears after polling).
  await expect(page.getByText(/入库完成/)).toBeVisible({ timeout: 60_000 })

  // Knowledge-base chat enters the RAG agent preset.
  await page.getByRole('button', { name: /去知识库问答/ }).click()
  await page.getByLabel('输入消息').fill('退款政策是什么')
  await page.getByRole('button', { name: '运行 Agent' }).click()

  // The RAG preset forces knowledge_search; the mock then final-answers.
  await expect(page.getByText('这是 Mock Provider 的最终回答。')).toBeVisible({
    timeout: 60_000,
  })
})
