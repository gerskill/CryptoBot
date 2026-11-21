import { describe, expect, it } from 'vitest'
import {
  ariaSortOf,
  countLabel,
  keepOrder,
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
  it('première pression : sens initial paramétrable (P&L décroissant d’abord)', () => {
    expect(nextSort(null, 'pnl', 'desc')).toEqual({ key: 'pnl', dir: 'desc' })
    expect(nextSort({ key: 'pnl', dir: 'desc' }, 'pnl', 'desc')).toEqual({ key: 'pnl', dir: 'asc' })
    expect(nextSort({ key: 'nom', dir: 'asc' }, 'pnl', 'desc')).toEqual({ key: 'pnl', dir: 'desc' })
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
  it('NaN et Infinity sont des valeurs absentes : toujours en dernier', () => {
    const lignes = [
      { n: 'nan', v: NaN }, { n: 'un', v: 1 }, { n: 'inf', v: Infinity }, { n: 'deux', v: 2 },
    ]
    const accesseurs = { v: (r: (typeof lignes)[0]) => r.v }
    expect(sortRows(lignes, accesseurs, { key: 'v', dir: 'asc' }).map((r) => r.n)).toEqual(['un', 'deux', 'nan', 'inf'])
    expect(sortRows(lignes, accesseurs, { key: 'v', dir: 'desc' }).map((r) => r.n)).toEqual(['deux', 'un', 'nan', 'inf'])
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

describe('keepOrder', () => {
  const rows = [{ id: 'a' }, { id: 'b' }, { id: 'c' }]
  it('garde l’ordre figé, valeurs à jour', () => {
    expect(keepOrder(rows, (r) => r.id, ['c', 'a', 'b']).map((r) => r.id)).toEqual(['c', 'a', 'b'])
  })
  it('ignore un id disparu et range un nouvel arrivant à la fin', () => {
    expect(keepOrder(rows, (r) => r.id, ['z', 'b', 'a']).map((r) => r.id)).toEqual(['b', 'a', 'c'])
  })
})
