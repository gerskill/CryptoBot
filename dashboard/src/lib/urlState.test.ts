import { describe, expect, it } from 'vitest'
import { DEFAULT_URL_STATE, historyAction, parseUrlState, serializeUrlState } from './urlState'

describe('parseUrlState', () => {
  it('lit un onglet memecoins valide', () => {
    expect(parseUrlState('?tab=weekly')).toEqual({ view: 'weekly' })
  })

  it('lit un onglet Liquid quand l’espace est liquid', () => {
    const state = parseUrlState('?space=liquid&tab=marches&famille=sport')
    expect(state).toMatchObject({ space: 'liquid', liquidView: 'marches' })
    expect(state.filters).toEqual({ famille: 'sport' })
  })

  it('ignore un onglet qui n’appartient pas à l’espace', () => {
    expect(parseUrlState('?space=liquid&tab=weekly')).toEqual({ space: 'liquid' })
  })

  it('ignore proprement les valeurs invalides', () => {
    const state = parseUrlState('?space=pirate&tab=<script>&window=9000d&famille=zzz&statut=x&panel=')
    expect(state).toEqual({})
  })

  it('lit fenêtre, panneau et recherche', () => {
    const state = parseUrlState('?window=30d&panel=sniper&q=%20btc%20')
    expect(state).toMatchObject({ window: '30d', panel: 'sniper', filters: { q: 'btc' } })
  })

  it('tronque et nettoie les textes libres', () => {
    const long = 'a'.repeat(500)
    expect(parseUrlState(`?q=${long}`).filters?.q).toHaveLength(60)
    expect(parseUrlState('?panel=a%00b%0Ac').panel).toBe('abc')
  })

  it('ne plante jamais sur une chaîne bricolée', () => {
    for (const raw of ['', '?', '??&&==', '?%E0%A4%A', '?tab=%', 'tab', '?a[]=1&tab[]=live']) {
      expect(() => parseUrlState(raw)).not.toThrow()
    }
  })
})

describe('serializeUrlState', () => {
  it('omet les valeurs par défaut', () => {
    expect(serializeUrlState(DEFAULT_URL_STATE)).toBe('')
  })

  it('n’écrit que l’onglet de l’espace actif', () => {
    const state = { ...DEFAULT_URL_STATE, space: 'liquid' as const, liquidView: 'paris' as const, view: 'weekly' as const }
    expect(serializeUrlState(state)).toBe('?space=liquid&tab=paris')
  })

  it('fait l’aller-retour avec parseUrlState', () => {
    const state = {
      ...DEFAULT_URL_STATE, view: 'weekly' as const, window: 'all' as const,
      panel: 'sniper', filters: { q: 'btc usd' },
    }
    const back = parseUrlState(serializeUrlState(state))
    expect(back).toMatchObject({ view: 'weekly', window: 'all', panel: 'sniper', filters: { q: 'btc usd' } })
  })
})

describe('filtres par défaut', () => {
  it('ne s’écrivent ni ne se relisent', () => {
    expect(serializeUrlState({ ...DEFAULT_URL_STATE, filters: { famille: 'prix', statut: 'tous' } })).toBe('')
    expect(parseUrlState('?famille=prix&statut=tous')).toEqual({})
  })
})

describe('historyAction', () => {
  const a = DEFAULT_URL_STATE
  it('ne fait rien si rien ne change', () => {
    expect(historyAction(a, { ...a })).toBe('none')
  })
  it('pousse une entrée quand l’onglet ou l’espace change', () => {
    expect(historyAction(a, { ...a, view: 'live' })).toBe('push')
    expect(historyAction(a, { ...a, space: 'liquid' })).toBe('push')
    expect(historyAction({ ...a, space: 'liquid' }, { ...a, space: 'liquid', liquidView: 'paris' })).toBe('push')
    expect(historyAction(a, { ...a, liquidView: 'paris' })).toBe('none')
  })
  it('remplace pour fenêtre, panneau et filtres', () => {
    expect(historyAction(a, { ...a, window: '30d' })).toBe('replace')
    expect(historyAction(a, { ...a, panel: 'x' })).toBe('replace')
    expect(historyAction(a, { ...a, filters: { q: 'x' } })).toBe('replace')
  })
})
