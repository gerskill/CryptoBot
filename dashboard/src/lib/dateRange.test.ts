import { describe, expect, it } from 'vitest'
import { filterByRange, isInvalidRange, parseUtc, pnlBefore, resolveRange } from './dateRange'

const NOW = new Date('2026-11-21T12:00:00Z')
const t = (ts: string, pnl = 1) => ({ timestamp_exit: ts, pnl_usd: pnl })

describe('resolveRange', () => {
  it('7 j et 30 j reculent depuis maintenant', () => {
    const r = resolveRange({ preset: '7d' }, NOW)
    expect(r.until).toBeNull()
    expect(r.since).toBe('2026-11-14T12:00:00.000Z')
    expect(resolveRange({ preset: '30d' }, NOW).since).toBe('2026-10-22T12:00:00.000Z')
  })
  it('tout = aucune borne', () => {
    expect(resolveRange({ preset: 'all' }, NOW)).toEqual({ since: null, until: null })
  })
  it('dates personnalisées : fin INCLUSIVE (lendemain minuit local exclu)', () => {
    const r = resolveRange({ preset: 'custom', from: '2026-11-01', to: '2026-11-03' }, NOW)
    expect(r.since).toBe(new Date(2026, 10, 1).toISOString())
    expect(r.until).toBe(new Date(2026, 10, 4).toISOString())
  })
  it('date vide ou illisible = borne ouverte', () => {
    expect(resolveRange({ preset: 'custom', from: '', to: 'nope' }, NOW)).toEqual({ since: null, until: null })
  })
})

describe('filterByRange', () => {
  const rows = [t('2026-11-01T00:00:00Z'), t('2026-11-10T00:00:00Z'), t('2026-11-20T00:00:00Z'), t('')]
  it('borne basse incluse, borne haute exclue', () => {
    const out = filterByRange(rows, { since: '2026-11-10T00:00:00Z', until: '2026-11-20T00:00:00Z' })
    expect(out.map((r) => r.timestamp_exit)).toEqual(['2026-11-10T00:00:00Z'])
  })
  it('sans borne : tout ce qui a une date valide, sans muter l\'entrée', () => {
    const out = filterByRange(rows, { since: null, until: null })
    expect(out).toHaveLength(3)
    expect(rows).toHaveLength(4)
  })
})

describe('pnlBefore', () => {
  it('somme les P&L antérieurs à la borne', () => {
    const rows = [t('2026-11-01T00:00:00Z', 5), t('2026-11-10T00:00:00Z', -2), t('2026-11-20T00:00:00Z', 9)]
    expect(pnlBefore(rows, '2026-11-10T00:00:00Z')).toBe(5)
    expect(pnlBefore(rows, null)).toBe(0)
  })
})

describe('isInvalidRange', () => {
  it('début après (ou égal à) la fin', () => {
    expect(isInvalidRange({ since: '2026-11-10T00:00:00Z', until: '2026-11-09T00:00:00Z' })).toBe(true)
    expect(isInvalidRange({ since: '2026-11-10T00:00:00Z', until: '2026-11-10T00:00:00Z' })).toBe(true)
    expect(isInvalidRange({ since: null, until: '2026-11-09T00:00:00Z' })).toBe(false)
    expect(isInvalidRange({ since: '2026-11-01T00:00:00Z', until: '2026-11-09T00:00:00Z' })).toBe(false)
  })
})

describe('parseUtc', () => {
  it('ISO naïf = UTC ; avec fuseau ou Z inchangé', () => {
    expect(parseUtc('2026-11-10T00:00:00')).toBe(Date.parse('2026-11-10T00:00:00Z'))
    expect(parseUtc('2026-11-10 00:00:00')).toBe(Date.parse('2026-11-10T00:00:00Z'))
    expect(parseUtc('2026-11-10T02:00:00+02:00')).toBe(Date.parse('2026-11-10T00:00:00Z'))
    expect(parseUtc('2026-11-10T00:00:00Z')).toBe(Date.parse('2026-11-10T00:00:00Z'))
  })
})
