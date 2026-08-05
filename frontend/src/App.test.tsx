import '@testing-library/jest-dom/vitest'

import { cleanup, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import App from './App.tsx'

afterEach(() => {
  cleanup()
})

function getMetricValue(label: string): HTMLElement {
  const labelElement = screen.getByText(label)
  const metricElement = labelElement.parentElement

  if (!metricElement) {
    throw new Error(`Missing metric container for ${label}`)
  }

  return within(metricElement).getByText(/\d+/)
}

describe('App', () => {
  it('updates the local session state when creating a session', async () => {
    const user = userEvent.setup()

    render(<App />)

    expect(screen.getByText('无本地会话')).toBeInTheDocument()
    expect(getMetricValue('会话数')).toHaveTextContent('0')
    expect(screen.getByText('尚未创建会话')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: '新建会话' }))

    expect(screen.getByText('本地会话 1')).toBeInTheDocument()
    expect(getMetricValue('会话数')).toHaveTextContent('1')
    expect(screen.getByText('已创建一个本地空会话')).toBeInTheDocument()
  })

  it('clears the local console state and increments the cleared count', async () => {
    const user = userEvent.setup()

    render(<App />)

    const createSessionButton = screen.getByRole('button', { name: '新建会话' })

    await user.click(createSessionButton)
    await user.click(createSessionButton)

    expect(screen.getByText('本地会话 2')).toBeInTheDocument()
    expect(getMetricValue('会话数')).toHaveTextContent('2')
    expect(getMetricValue('清空次数')).toHaveTextContent('0')

    await user.click(screen.getByRole('button', { name: '清空' }))

    expect(screen.getByText('无本地会话')).toBeInTheDocument()
    expect(getMetricValue('会话数')).toHaveTextContent('0')
    expect(getMetricValue('清空次数')).toHaveTextContent('1')
    expect(screen.getByText('已清空本地控制台状态')).toBeInTheDocument()
  })
})
