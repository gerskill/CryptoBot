import { describe, expect, it } from 'vitest'
import { buildComparison, MAX_COMPARED, MIN_COMPARED, toggleSelection } from './armCompare'

const tr = (ts: string, pnl: number) => ({ timestamp_exit: ts, pnl_usd: pnl })

describe('toggleSelection', () => {
  it('bornes 2 à 4', () => {
    expect(MIN_COMPARED).toBe(2)
    expect(MAX_COMPARED).toBe(4)
  })
  it('ajoute puis retire, sans muter', () => {
    const start = ['a', 'b']
    const added = toggleSelection(start, 'c')
    expect(added).toEqual(['a', 'b', 'c'])
    expect(start).toEqual(['a', 'b'])
    expect(toggleSelection(added, 'a')).toEqual(['b', 'c'])
  })
  it('refuse un cinquième bras', () => {
    const four = ['a', 'b', 'c', 'd']
    expect(toggleSelection(four, 'e')).toEqual(four)
  })
})

describe('buildComparison — baseline et fuseau', () => {
  it('baseline inconnue : drawdown non calculé, rien d\'inventé', () => {
    const [a] = buildComparison([{ arm: 'a', baseline: null, trades: [tr('2026-11-01T00:00:00Z', -5)] }], { since: null, until: null })
    expect(a.baselineKnown).toBe(false)
    expect(a.drawdown).toEqual([])
    expect(a.summary.maxDrawdownPct).toBe(0)
  })
  it('timestamp sans fuseau lu comme UTC, comme le serveur', () => {
    const [a] = buildComparison(
      [{ arm: 'a', baseline: 1000, trades: [tr('2026-11-10T00:30:00', 1), tr('2026-11-09T23:30:00', 2)] }],
      { since: '2026-11-10T00:00:00Z', until: null },
    )
    expect(a.series).toEqual([1])
  })
})

describe('buildComparison', () => {
  const inputs = [
    {
      arm: 'a',
      baseline: 1000,
      trades: [tr('2026-11-01T00:00:00Z', -100), tr('2026-11-10T00:00:00Z', 50), tr('2026-11-12T00:00:00Z', -25)],
    },
    { arm: 'b', baseline: 500, trades: [] },
  ]
  it('restreint à la plage, courbe cumulée partant de 0', () => {
    const [a] = buildComparison(inputs, { since: '2026-11-05T00:00:00Z', until: null })
    expect(a.series).toEqual([50, -25])
    expect(a.points).toEqual([0, 50, 25])
    expect(a.summary.trades).toBe(2)
    expect(a.summary.pnlTotal).toBe(25)
  })
  it('le drawdown part de la mise + P&L réalisé avant la plage', () => {
    const [a] = buildComparison(inputs, { since: '2026-11-05T00:00:00Z', until: null })
    // départ 900 -> pic 950 -> 925 : 2.63 %
    expect(a.summary.maxDrawdownPct).toBeCloseTo(2.63, 1)
  })
  it('bras sans trade sur la plage : série vide, pas de chiffre inventé', () => {
    const b = buildComparison(inputs, { since: null, until: null })[1]
    expect(b.series).toEqual([])
    expect(b.summary.trades).toBe(0)
  })
  it('trie chronologiquement même si l\'entrée est inversée', () => {
    const rev = [{ arm: 'a', baseline: 1000, trades: [tr('2026-11-02T00:00:00Z', 2), tr('2026-11-01T00:00:00Z', 1)] }]
    expect(buildComparison(rev, { since: null, until: null })[0].series).toEqual([1, 2])
  })
})
