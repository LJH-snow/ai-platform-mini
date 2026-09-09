import { expect, test, type Page } from '@playwright/test'

/** Register a fresh user through the auth UI and land in the console. */
async function register(page: Page, email: string): Promise<void> {
  await page.goto('/')
  await page.getByRole('button', { name: '登录 / 注册' }).click()
  await expect(page.getByRole('heading', { name: '登录' })).toBeVisible()
  await page.getByRole('button', { name: '注册' }).click()
  await expect(page.getByRole('heading', { name: '注册' })).toBeVisible()
  await page.getByLabel('邮箱').fill(email)
  await page.getByLabel('显示名称').fill(email.split('@')[0])
  await page.getByLabel('密码').fill('secret123')
  await page.getByRole('button', { name: '注册', exact: true }).click()
  await expect(page.getByRole('button', { name: '对话工作台', exact: true })).toBeVisible({
    timeout: 30_000,
  })
}

test('create a new workspace via the workspace management page', async ({ page }) => {
  await register(page, `e2e-ws-create-${Date.now()}@test.com`)

  // Navigate to workspace management
  await page.getByRole('button', { name: '工作空间', exact: true }).click()

  // Create a new workspace
  await page.getByPlaceholder('新工作空间名称').fill('E2E Test Workspace')
  await page.getByRole('button', { name: '创建工作空间' }).click()

  // The new workspace should appear in the list
  await expect(page.getByText('E2E Test Workspace')).toBeVisible({ timeout: 30_000 })
})

test('invite a member to workspace', async ({ page }) => {
  const timestamp = Date.now()
  await register(page, `e2e-invite-owner-${timestamp}@test.com`)

  // Navigate to workspace management
  await page.getByRole('button', { name: '工作空间', exact: true }).click()

  // Invite a new member
  await page.getByPlaceholder('成员邮箱').fill(`e2e-invite-member-${timestamp}@test.com`)
  await page.getByLabel('角色').selectOption('member')
  await page.getByRole('button', { name: '添加成员' }).click()

  // The member should appear in the list
  await expect(page.getByText(`e2e-invite-member-${timestamp}@test.com`)).toBeVisible({
    timeout: 30_000,
  })
})
