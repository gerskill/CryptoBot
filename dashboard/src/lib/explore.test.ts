import { describe, expect, it } from 'vitest'
import {
  ariaSortOf,
  countLabel,
  matchesQuery,
  nextSort,
  normalize,
  sortRows,
} from './explore'

describe('normalize', () => {
  it('ignore la casse et les accents', () => {
    expect(normalize('  Éthérium ')).toBe('etherium')
  })
})

describe('matchesQuery', () => {
  it('requête vide : tout passe', () => {
    expect(matchesQuery(['PEPE'], '   ')).toBe(true)
  })
  it('cherche dans plusieurs champs, sans casse ni accent', () => {
    expect(matchesQuery(['sniper', 'Règle fixe', 12], 'regle')).toBe(true)
    expect(matchesQuery(['sniper', null, undefined], 'SNIP')).toBe(true)
  })
  it('tous les mots doivent être trouvés (ET)', () => {
    expect(matchesQuery(['BTC au-dessus de 100k'], 'btc 100k')).toBe(true)
    expect(matchesQuery(['BTC au-dessus de 100k'], 'btc eth')).toBe(false)
  })
  it('aucun champ ne correspond : faux', () => {
    expect(matchesQuery(['pepe'], 'doge')).toBe(false)
  })
})

describe('nextSort', () => {
  it('première pression : ascendant', () => {
    expect(nextSort(null, 'pnl')).toEqual({ key: 'pnl', dir: 'asc' })
  })
  it('même colonne : bascule asc puis desc, sans repasser par rien', () => {
    expect(nextSort({ key: 'pnl', dir: 'asc' }, 'pnl')).toEqual({ key: 'pnl', dir: 'desc' })
    expect(nextSort({ key: 'pnl', dir: 'desc' }, 'pnl')).toEqual({ key: 'pnl', dir: 'asc' })
  })
  it('autre colonne : repart en ascendant', () => {
    expect(nextSort({ key: 'pnl', dir: 'desc' }, 'nom')).toEqual({ key: 'nom', dir: 'asc' })
  })
})

describe('ariaSortOf', () => {
  it('reflète la colonne triée, none ailleurs', () => {
    const sort = { key: 'a', dir: 'desc' } as const
    expect(ariaSortOf(sort, 'a')).toBe('descending')
    expect(ariaSortOf({ key: 'a', dir: 'asc' }, 'a')).toBe('ascending')
    expect(ariaSortOf(sort, 'b')).toBe('none')
    expect(ariaSortOf(null, 'a')).toBe('none')
  })
})

describe('sortRows', () => {
  const rows = [
    { n: 'b', v: 2 },
    { n: 'a', v: null as number | null },
    { n: 'c', v: 10 },
  ]
  const acc = { n: (r: (typeof rows)[0]) => r.n, v: (r: (typeof rows)[0]) => r.v }

  it('sans tri : même ordre, nouvelle référence', () => {
    const out = sortRows(rows, acc, null)
    expect(out).toEqual(rows)
    expect(out).not.toBe(rows)
  })
  it('numérique ascendant, valeurs absentes toujours en dernier', () => {
    expect(sortRows(rows, acc, { key: 'v', dir: 'asc' }).map((r) => r.n)).toEqual(['b', 'c', 'a'])
    expect(sortRows(rows, acc, { key: 'v', dir: 'desc' }).map((r) => r.n)).toEqual(['c', 'b', 'a'])
  })
  it('texte avec locale française', () => {
    const out = sortRows(rows, acc, { key: 'n', dir: 'desc' }).map((r) => r.n)
    expect(out).toEqual(['c', 'b', 'a'])
  })
  it('ne mute pas l’entrée', () => {
    const copie = [...rows]
    sortRows(rows, acc, { key: 'v', dir: 'asc' })
    expect(rows).toEqual(copie)
  })
  it('clé inconnue : ordre d’origine', () => {
    expect(sortRows(rows, acc, { key: 'zzz', dir: 'asc' })).toEqual(rows)
  })
  it('tri stable à valeur égale', () => {
    const egaux = [{ n: 'x', v: 1 }, { n: 'y', v: 1 }, { n: 'z', v: 1 }]
    expect(sortRows(egaux, acc, { key: 'v', dir: 'desc' }).map((r) => r.n)).toEqual(['x', 'y', 'z'])
  })
})

describe('countLabel', () => {
  it('N sur M', () => {
    expect(countLabel(3, 12)).toBe('3 sur 12')
  })
})
