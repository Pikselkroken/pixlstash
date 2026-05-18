import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { copyText } from './clipboard.js'

// ── helpers ──────────────────────────────────────────────────────────────────

function setUserAgent(ua) {
  Object.defineProperty(navigator, 'userAgent', {
    value: ua,
    configurable: true,
  })
}

// ── Windows newline normalization ─────────────────────────────────────────────

describe('Windows newline normalization', () => {
  let captured = ''

  beforeEach(() => {
    setUserAgent('Mozilla/5.0 (Windows NT 10.0)')
    captured = ''
    // Stub clipboard API so we can inspect what was written.
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: vi.fn(async (t) => { captured = t }),
      },
      configurable: true,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('converts bare \\n to \\r\\n on Windows', async () => {
    await copyText('line1\nline2\nline3')
    expect(captured).toBe('line1\r\nline2\r\nline3')
  })

  it('does not double-convert already-normalized \\r\\n', async () => {
    await copyText('line1\r\nline2\r\nline3')
    expect(captured).toBe('line1\r\nline2\r\nline3')
  })

  it('handles mixed \\n and \\r\\n in the same string', async () => {
    await copyText('a\nb\r\nc')
    expect(captured).toBe('a\r\nb\r\nc')
  })
})

// ── no normalization on non-Windows ──────────────────────────────────────────

describe('non-Windows: no newline normalization', () => {
  let captured = ''

  beforeEach(() => {
    setUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)')
    captured = ''
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: vi.fn(async (t) => { captured = t }),
      },
      configurable: true,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('leaves \\n unchanged on non-Windows', async () => {
    await copyText('line1\nline2')
    expect(captured).toBe('line1\nline2')
  })
})

// ── Clipboard API path ────────────────────────────────────────────────────────

describe('navigator.clipboard.writeText path', () => {
  beforeEach(() => {
    setUserAgent('Mozilla/5.0 (X11; Linux x86_64)')
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('returns true when clipboard API succeeds', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(async () => {}) },
      configurable: true,
    })
    expect(await copyText('hello')).toBe(true)
  })

  it('falls through to execCommand when clipboard API throws', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: vi.fn(async () => { throw new Error('denied') }),
      },
      configurable: true,
    })
    // jsdom's execCommand is a no-op stub — it won't fire a copy event, so
    // the handler never sets intercepted=true.  We just verify it doesn't throw.
    const result = await copyText('hello')
    expect(typeof result).toBe('boolean')
  })

  it('returns false for empty string', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn(async () => {}) },
      configurable: true,
    })
    expect(await copyText('')).toBe(false)
    expect(await copyText(null)).toBe(false)
  })
})

// ── execCommand fallback path ─────────────────────────────────────────────────

describe('execCommand fallback path', () => {
  beforeEach(() => {
    setUserAgent('Mozilla/5.0 (X11; Linux x86_64)')
    // Remove clipboard API to force the fallback.
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      configurable: true,
    })
  })

  afterEach(() => {
    delete document.execCommand
    vi.restoreAllMocks()
  })

  it('calls document.execCommand("copy") when clipboard API is unavailable', async () => {
    // jsdom doesn't define document.execCommand, so assign a stub directly.
    // We return false here (no real copy event fired), so intercepted stays false.
    document.execCommand = vi.fn(() => false)

    await copyText('test text')

    expect(document.execCommand).toHaveBeenCalledWith('copy')
  })
})
