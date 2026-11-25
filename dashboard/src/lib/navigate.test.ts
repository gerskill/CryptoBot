import { beforeEach, describe, expect, it } from 'vitest'
import { useStore } from './store'
import { navigateTo } from './navigate'
import { DEFAULT_URL_STATE } from './urlState'

describe('navigation et état local à la vue', () => {
  beforeEach(() => useStore.setState({ ...DEFAULT_URL_STATE, panel: 'sniper', filters: { q: 'btc' } }))

  it('un changement d’onglet efface panneau et filtres', () => {
    useStore.getState().setView('weekly')
    expect(useStore.getState().panel).toBeNull()
    expect(useStore.getState().filters).toEqual({})
  })
  it('un changement d’espace aussi', () => {
    useStore.getState().setSpace('liquid')
    expect(useStore.getState().panel).toBeNull()
  })
  it('navigateTo efface sauf cible explicite', () => {
    navigateTo({ view: 'live' }, 'Live')
    expect(useStore.getState().panel).toBeNull()
    navigateTo({ view: 'dashboard', panel: 'runner' }, 'Bras runner')
    expect(useStore.getState().panel).toBe('runner')
  })
  it('rester sur le même onglet conserve le contexte', () => {
    useStore.getState().setView('compte')
    expect(useStore.getState().panel).toBe('sniper')
  })
})
