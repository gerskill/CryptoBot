import { describe, expect, it } from 'vitest'
import { defaultPicked, fleetInput } from './account'

const tr = (ts: string, pnl: number) => ({ timestamp_exit: ts, pnl_usd: pnl })

describe('fleetInput', () => {
  it('fusionne les trades de tous les bras et somme les mises', () => {
    const input = fleetInput([
      { arm: 'a', baseline: 100, trades: [tr('2026-11-22T10:00:00Z', 1)] },
      { arm: 'b', baseline: 100, trades: [tr('2026-11-22T09:00:00Z', -2)] },
    ])
    expect(input.arm).toBe('compte')
    expect(input.baseline).toBe(200)
    expect(input.trades.map((t) => t.pnl_usd)).toEqual([1, -2])
  })
  it('baseline inconnue pour un bras : drawdown du compte non calculé', () => {
    const input = fleetInput([
      { arm: 'a', baseline: 100, trades: [] },
      { arm: 'b', baseline: null, trades: [] },
    ])
    expect(input.baseline).toBeNull()
  })
  it('aucun bras : baseline nulle, aucun trade', () => {
    expect(fleetInput([])).toEqual({ arm: 'compte', baseline: null, trades: [] })
  })
  it('ne mute pas ses entrées', () => {
    const trades = [tr('2026-11-22T10:00:00Z', 1)]
    fleetInput([{ arm: 'a', baseline: 100, trades }])
    expect(trades).toHaveLength(1)
  })
})

describe('defaultPicked', () => {
  const s = (arm: string, trades: number, pnlTotal: number) => ({
    arm, trades, pnlTotal, winRate: 0, profitFactor: 0, maxDrawdownPct: 0,
  })
  it('prend au plus 4 bras actifs, plus gros |P&L| d\'abord', () => {
    const picked = defaultPicked([s('a', 3, 1), s('b', 0, 0), s('c', 5, -9), s('d', 1, 4), s('e', 2, 2), s('f', 9, 0.5)])
    expect(picked).toEqual(['c', 'd', 'e', 'a'])
  })
  it('aucun bras actif : liste vide', () => {
    expect(defaultPicked([s('a', 0, 0)])).toEqual([])
  })
})
