import { describe, expect, it, vi } from 'vitest'

import { createAdminClient } from './client.ts'

const okJson = (payload: unknown): Response =>
  new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })

describe('admin workspace quota client', () => {
  it('reads the quota for a workspace', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson({
        workspace_id: 'ws-1',
        daily_token_limit: 5000,
        monthly_token_limit: null,
      }),
    )
    const client = createAdminClient({
      apiBaseUrl: 'http://localhost:8000',
      apiKey: 'sk-admin',
      fetchImpl,
    })

    const quota = await client.getWorkspaceQuota('ws-1')

    expect(fetchImpl).toHaveBeenCalledWith(
      'http://localhost:8000/admin/workspaces/ws-1/quota',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer sk-admin' }),
      }),
    )
    expect(quota.daily_token_limit).toBe(5000)
    expect(quota.monthly_token_limit).toBeNull()
  })

  it('writes the quota with a PUT body and null clearing', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson({
        workspace_id: 'ws-1',
        daily_token_limit: null,
        monthly_token_limit: null,
      }),
    )
    const client = createAdminClient({
      apiBaseUrl: 'http://localhost:8000',
      apiKey: 'sk-admin',
      fetchImpl,
    })

    await client.setWorkspaceQuota('ws-1', {
      daily_token_limit: null,
      monthly_token_limit: null,
    })

    expect(fetchImpl).toHaveBeenCalledWith(
      'http://localhost:8000/admin/workspaces/ws-1/quota',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          daily_token_limit: null,
          monthly_token_limit: null,
        }),
      }),
    )
  })

  it('lists audit events with query parameters', async () => {
    const fetchImpl = vi.fn(async () => okJson([]))
    const client = createAdminClient({ apiKey: 'sk-admin', fetchImpl })

    await client.listAuditEvents({ workspace_id: 'ws-1', action: 'agent.update', limit: 20 })

    expect(fetchImpl).toHaveBeenCalledWith(
      '/admin/audit-events?workspace_id=ws-1&action=agent.update&limit=20',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer sk-admin' }),
      }),
    )
  })

  it('encodes the workspace id in the path', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson({
        workspace_id: 'ws 1',
        daily_token_limit: null,
        monthly_token_limit: null,
      }),
    )
    const client = createAdminClient({ apiKey: 'sk-admin', fetchImpl })

    await client.getWorkspaceQuota('ws 1')

    expect(fetchImpl).toHaveBeenCalledWith('/admin/workspaces/ws%201/quota', expect.anything())
  })

  it('lists admin plans', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson([
        {
          id: 'plan-free',
          name: 'Free',
          version: 1,
          daily_token_limit: 5000,
          monthly_token_limit: null,
          max_agents: 1,
          max_documents: 10,
          max_members: 3,
          features: { rag: true },
        },
      ]),
    )
    const client = createAdminClient({ apiKey: 'sk-admin', fetchImpl })

    const plans = await client.listPlans()

    expect(fetchImpl).toHaveBeenCalledWith(
      '/admin/plans',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer sk-admin' }),
      }),
    )
    expect(plans[0]?.name).toBe('Free')
  })

  it('lists subscriptions with query parameters', async () => {
    const fetchImpl = vi.fn(async () => okJson([]))
    const client = createAdminClient({ apiKey: 'sk-admin', fetchImpl })

    await client.listSubscriptions({ plan_id: 'plan-pro', status: 'ACTIVE', limit: 20 })

    expect(fetchImpl).toHaveBeenCalledWith(
      '/admin/subscriptions?plan_id=plan-pro&status=ACTIVE&limit=20',
      expect.objectContaining({
        headers: expect.objectContaining({ Authorization: 'Bearer sk-admin' }),
      }),
    )
  })

  it('assigns a subscription with POST body and encoded workspace id', async () => {
    const fetchImpl = vi.fn(async () =>
      okJson({
        id: 'sub-1',
        workspace_id: 'ws 1',
        plan_id: 'plan-pro',
        plan_name: 'Pro',
        status: 'TRIAL',
        started_at: null,
        expired_at: null,
      }),
    )
    const client = createAdminClient({ apiKey: 'sk-admin', fetchImpl })

    const subscription = await client.assignSubscription('ws 1', 'plan-pro', 'TRIAL')

    expect(fetchImpl).toHaveBeenCalledWith(
      '/admin/workspaces/ws%201/subscription',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ plan_id: 'plan-pro', status: 'TRIAL' }),
      }),
    )
    expect(subscription.plan_name).toBe('Pro')
  })
})
