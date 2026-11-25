import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, renderHook } from '@testing-library/react'
import { useUrlSync } from './useUrlSync'
import { useStore } from './store'
import { DEFAULT_URL_STATE } from './urlState'

function reset() {
  useStore.setState({ ...DEFAULT_URL_STATE })
}

describe('useUrlSync', () => {
  beforeEach(() => {
    reset()
    window.history.replaceState(null, '', '/')
  })
  afterEach(cleanup)

  it('lit l’URL au chargement', () => {
    window.history.replaceState(null, '', '/?space=liquid&tab=marches&famille=sport&q=btc')
    renderHook(() => useUrlSync())
    const s = useStore.getState()
    expect(s.space).toBe('liquid')
    expect(s.liquidView).toBe('marches')
    expect(s.filters).toEqual({ famille: 'sport', q: 'btc' })
  })

  it('ignore une URL bricolée et la remet au propre', () => {
    window.history.replaceState(null, '', '/?space=x&tab=<b>&window=999&famille=%00')
    renderHook(() => useUrlSync())
    expect(useStore.getState().view).toBe('compte')
    expect(window.location.search).toBe('')
  })

  it('pousse une entrée pour l’onglet et remplace pour la fenêtre', () => {
    renderHook(() => useUrlSync())
    const before = window.history.length
    act(() => useStore.getState().setView('weekly'))
    expect(window.location.search).toBe('?tab=weekly')
    expect(window.history.length).toBe(before + 1)
    act(() => useStore.getState().setWindow('30d'))
    expect(window.location.search).toBe('?tab=weekly&window=30d')
    expect(window.history.length).toBe(before + 1)
  })

  it('ne plante pas si l’historique lève une exception (Safari)', () => {
    renderHook(() => useUrlSync())
    const spy = vi.spyOn(window.history, 'pushState').mockImplementation(() => { throw new DOMException('x', 'SecurityError') })
    expect(() => act(() => useStore.getState().setView('weekly'))).not.toThrow()
    expect(useStore.getState().view).toBe('weekly')
    spy.mockRestore()
  })

  it('temporise le replaceState de la recherche q', () => {
    vi.useFakeTimers()
    try {
      renderHook(() => useUrlSync())
      const spy = vi.spyOn(window.history, 'replaceState')
      spy.mockClear()
      act(() => useStore.getState().setFilter('q', 'b'))
      act(() => useStore.getState().setFilter('q', 'bt'))
      act(() => useStore.getState().setFilter('q', 'btc'))
      expect(spy).not.toHaveBeenCalled()
      act(() => { vi.advanceTimersByTime(500) })
      expect(spy).toHaveBeenCalledTimes(1)
      expect(window.location.search).toBe('?q=btc')
      spy.mockRestore()
    } finally {
      vi.useRealTimers()
    }
  })

  it('met le store à jour sur Précédent (popstate)', () => {
    renderHook(() => useUrlSync())
    act(() => useStore.getState().setView('weekly'))
    window.history.replaceState(null, '', '/')
    act(() => { window.dispatchEvent(new PopStateEvent('popstate')) })
    expect(useStore.getState().view).toBe('compte')
    expect(window.location.search).toBe('')
  })
})
