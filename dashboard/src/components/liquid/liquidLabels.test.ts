import { describe, expect, it } from 'vitest'
import { drawdownLabel, engagementBlocked, engagementLine, marketLine } from './liquidLabels'

describe('marketLine', () => {
  it('ne montre rien tant que le runner n’a rien valorisé', () => {
    expect(marketLine(undefined)).toBeNull()
    expect(marketLine({})).toBeNull()
  })
  it('donne la valeur au marché et le latent, séparés du réalisé', () => {
    const line = marketLine({ equite_marche: 493.91, pnl_latent: -6.09, methodes_valorisation: { carnet: 2 } })
    expect(line).toContain('au marché 493.91 $')
    expect(line).toContain('latent')
    expect(line).not.toContain('estimé')
  })
  it('signale les paris valorisés hors carnet', () => {
    const line = marketLine({ equite_marche: 500, pnl_latent: 0, methodes_valorisation: { carnet: 1, milieu: 2 } })
    expect(line).toContain('2 estimés hors carnet')
  })
})

describe('drawdownLabel', () => {
  it('affiche la baisse depuis le plus haut', () => {
    expect(drawdownLabel({ drawdown_pic_pct: 56.364 })).toBe('56.4 %')
    expect(drawdownLabel(undefined)).toBe('—')
    expect(drawdownLabel({ drawdown_pic_pct: null })).toBe('—')
  })
})

describe('engagementLine', () => {
  it('montre engagé / limite et la part longue', () => {
    expect(engagementLine({ engage: 97.5, limite: 125, engage_long: 40, limite_long: 50, long_heures: 24 }))
      .toBe('engagé 97.50 / 125 $ · > 24 h 40.00 / 50 $')
    expect(engagementLine(null)).toBeNull()
  })
  it('signale un bras au-delà de la limite', () => {
    expect(engagementBlocked({ depasse: true, disponible: 0 })).toBe(true)
    expect(engagementBlocked({ depasse: false, disponible: 50 })).toBe(false)
  })
})
