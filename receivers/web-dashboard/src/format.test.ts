import { describe, it, expect } from 'vitest'
import { formatDuration, formatToolLine } from './types'

describe('formatDuration', () => {
  it('formats sub-minute as seconds', () => {
    expect(formatDuration(0)).toBe('0s')
    expect(formatDuration(45.4)).toBe('45s')
  })

  it('formats minutes with and without trailing seconds', () => {
    expect(formatDuration(120)).toBe('2m')
    expect(formatDuration(125)).toBe('2m5s')
  })

  it('formats hours with and without trailing minutes', () => {
    expect(formatDuration(7200)).toBe('2h')
    expect(formatDuration(7500)).toBe('2h5m')
  })
})

describe('formatToolLine', () => {
  it('uses a known icon and the tool name when no context', () => {
    expect(formatToolLine('Bash')).toBe('$ Bash')
    expect(formatToolLine('Read')).toBe('> Read')
  })

  it('falls back to # for unknown tools', () => {
    expect(formatToolLine('Mystery')).toBe('# Mystery')
  })

  it('prefers context over the tool name and truncates long context', () => {
    expect(formatToolLine('Bash', 'ls -la')).toBe('$ ls -la')
    const long = 'x'.repeat(100)
    const line = formatToolLine('Bash', long)
    expect(line.startsWith('$ ')).toBe(true)
    expect(line.endsWith('…')).toBe(true)
    expect(line.length).toBe(2 + 42 + 1) // "$ " + 42 chars + ellipsis
  })
})
