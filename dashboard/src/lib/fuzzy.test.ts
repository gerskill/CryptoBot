import { describe, expect, it } from 'vitest'
import { fuzzyScore, rankByQuery } from './fuzzy'

describe('fuzzyScore', () => {
  it('renvoie null sans correspondance', () => {
    expect(fuzzyScore('xyz', 'Weekly')).toBeNull()
  })
  it('accepte une requête vide (score neutre)', () => {
    expect(fuzzyScore('', 'Weekly')).toBe(0)
  })
  it('est insensible à la casse et aux accents', () => {
    expect(fuzzyScore('predic', 'Prédictions')).not.toBeNull()
    expect(fuzzyScore('MARCHES', 'Marchés')).not.toBeNull()
  })
  it('accepte une sous-séquence', () => {
    expect(fuzzyScore('wkly', 'Weekly')).not.toBeNull()
  })
  it('préfère le préfixe, puis le contigu, à l’éparpillé', () => {
    const prefix = fuzzyScore('con', 'Config') ?? -1
    const scattered = fuzzyScore('con', 'Stratégie consensus') ?? -1
    const loose = fuzzyScore('cng', 'Config') ?? -1
    expect(prefix).toBeGreaterThan(loose)
    expect(prefix).toBeGreaterThan(scattered - 0.0001)
  })
})

describe('rankByQuery', () => {
  const items = [
    { label: 'Config', keywords: [] },
    { label: 'Weekly', keywords: ['semaine'] },
    { label: 'Live Prices', keywords: ['prix'] },
  ]
  it('garde l’ordre d’origine sans requête', () => {
    expect(rankByQuery('', items).map((i) => i.label)).toEqual(['Config', 'Weekly', 'Live Prices'])
  })
  it('filtre et trie', () => {
    expect(rankByQuery('week', items).map((i) => i.label)).toEqual(['Weekly'])
  })
  it('cherche aussi dans les mots-clés', () => {
    expect(rankByQuery('semaine', items).map((i) => i.label)).toEqual(['Weekly'])
  })
  it('ne mute pas l’entrée', () => {
    const copy = [...items]
    rankByQuery('l', items)
    expect(items).toEqual(copy)
  })
})
