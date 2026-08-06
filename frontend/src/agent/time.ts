const AGENT_DISPLAY_TIME_ZONE = 'Asia/Shanghai'

const agentTimestampFormatter = new Intl.DateTimeFormat('en-CA', {
  timeZone: AGENT_DISPLAY_TIME_ZONE,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

export const formatAgentTimestamp = (value: string | null | undefined): string => {
  if (value === null || value === undefined) return '后端未提供'

  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value

  const parts = Object.fromEntries(
    agentTimestampFormatter
      .formatToParts(date)
      .map(({ type, value: partValue }) => [type, partValue]),
  )

  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}（上海）`
}
