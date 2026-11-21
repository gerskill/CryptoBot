import { describe, expect, it } from 'vitest'
import { buildDestinations } from './destinations'

describe('buildDestinations', () => {
  const list = buildDestinations({ arms: ['sniper', 'runner'], positions: ['BONK'] })
  const labels = list.map((d) => d.label)

  it('couvre onglets, espaces, bras et positions', () => {
    for (const l of ['Dashboard', 'Live Prices', 'Weekly', 'Config', 'Espace Memecoins', 'Espace Liquid']) {
      expect(labels).toContain(l)
    }
    expect(labels.some((l) => l.includes('sniper'))).toBe(true)
    expect(labels.some((l) => l.includes('BONK'))).toBe(true)
  })
  it('a des identifiants uniques', () => {
    expect(new Set(list.map((d) => d.id)).size).toBe(list.length)
  })
  it('n’inclut aucune action dangereuse', () => {
    const text = list.map((d) => `${d.label} ${(d.keywords ?? []).join(' ')}`).join(' ').toLowerCase()
    for (const bad of ['stop', 'arrêter', 'arreter', 'fermer', 'close', 'vendre', 'commande']) {
      expect(text).not.toContain(bad)
    }
  })
  it('chaque destination est une simple cible de navigation', () => {
    for (const d of list) expect(Object.keys(d.target).length).toBeGreaterThan(0)
  })
})
