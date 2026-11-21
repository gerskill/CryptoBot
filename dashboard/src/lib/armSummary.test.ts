import { describe, expect, it } from 'vitest'
import { summarizeArm } from './armSummary'

describe('summarizeArm', () => {
  it('calcule win rate, profit factor, P&L et drawdown', () => {
    const s = summarizeArm('a', [10, -20, 5], 1000)
    expect(s.trades).toBe(3)
    expect(s.winRate).toBeCloseTo(66.67, 1)
    expect(s.profitFactor).toBeCloseTo(0.75)
    expect(s.pnlTotal).toBe(-5)
    expect(s.maxDrawdownPct).toBeCloseTo(1.98, 1)
  })
  it('série vide ou sans perte', () => {
    expect(summarizeArm('a', [], 1000).trades).toBe(0)
    expect(summarizeArm('a', [5], 1000).profitFactor).toBe(Infinity)
  })
})
