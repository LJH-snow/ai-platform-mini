import { expect, test, type Page } from '@playwright/test'

// The mock LLM cannot produce a decomposition plan, so the supervisor falls
// back to a single writer subtask executed through the mock chat provider.
// The aggregated output is therefore the deterministic mock chat text.

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
  await expect(page.getByRole('button', { name: '对话工作台', exact: true })).toBeVisible({
    timeout: 30_000,
  })
}

test('multi-agent run streams the timeline and replays from history', async ({ page }) => {
  await register(page, `e2e-multi-agent-${Date.now()}@test.com`)

  await page.getByRole('button', { name: '多 Agent 编排', exact: true }).click()
  await expect(page.getByRole('heading', { name: '多 Agent 编排' })).toBeVisible()

  await page.getByLabel('任务描述').fill('调研向量数据库选型并输出对比报告')
  await page.getByRole('button', { name: '运行多 Agent', exact: true }).click()

  // Mock provider completes the run; the aggregated output is shown.
  await expect(page.getByText('Hello from Mock Provider').first()).toBeVisible({
    timeout: 60_000,
  })
  await expect(page.getByRole('heading', { name: '子任务时间线' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '汇总输出' })).toBeVisible()

  // The completed run is persisted and replayable from history.
  await page.getByRole('button', { name: '历史', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Run 历史' })).toBeVisible({
    timeout: 30_000,
  })
  const detail = page.getByRole('button', { name: '查看' }).first()
  await expect(detail).toBeVisible({ timeout: 30_000 })
  await detail.click()
  await expect(page.getByRole('heading', { name: 'Run 回放' })).toBeVisible({
    timeout: 30_000,
  })
  await expect(page.getByText('Hello from Mock Provider').last()).toBeVisible()
})
