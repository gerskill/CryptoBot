import { describe, expect, it } from 'vitest'
import { isNearStop, isNearTp1 } from './positionSignals'

const base = { stop_loss_pct: -20, take_profit_1: 50 }

describe('positionSignals', () => {
  it('signale un stop proche à mi-chemin du stop réel', () => {
    expect(isNearStop({ ...base, pnl_pct: -10 })).toBe(true)
    expect(isNearStop({ ...base, pnl_pct: -9.9 })).toBe(false)
  })
  it('signale TP1 proche à 70 % de la distance, jamais en même temps que le danger', () => {
    expect(isNearTp1({ ...base, pnl_pct: 35 })).toBe(true)
    expect(isNearTp1({ ...base, pnl_pct: 34 })).toBe(false)
    expect(isNearTp1({ ...base, pnl_pct: -15 })).toBe(false)
  })
})
