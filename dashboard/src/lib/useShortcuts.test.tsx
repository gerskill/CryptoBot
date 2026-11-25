import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, cleanup, fireEvent, renderHook } from '@testing-library/react'
import { useGlobalShortcuts } from './useShortcuts'
import { useStore } from './store'
import { DEFAULT_URL_STATE } from './urlState'

describe('useGlobalShortcuts', () => {
  beforeEach(() => {
    localStorage.clear()
    useStore.setState({ ...DEFAULT_URL_STATE, paletteOpen: false, helpOpen: false, shortcutsEnabled: true })
  })
  afterEach(cleanup)

  it('change d’onglet avec un chiffre', () => {
    renderHook(() => useGlobalShortcuts())
    fireEvent.keyDown(window, { key: '4' })
    expect(useStore.getState().view).toBe('weekly')
  })

  it('g puis lettre', () => {
    renderHook(() => useGlobalShortcuts())
    fireEvent.keyDown(window, { key: 'g' })
    fireEvent.keyDown(window, { key: 'c' })
    expect(useStore.getState().view).toBe('config')
  })

  it('bascule d’espace et ouvre palette et aide', () => {
    renderHook(() => useGlobalShortcuts())
    fireEvent.keyDown(window, { key: 's' })
    expect(useStore.getState().space).toBe('liquid')
    fireEvent.keyDown(window, { key: '/' })
    expect(useStore.getState().paletteOpen).toBe(true)
    act(() => useStore.getState().setPaletteOpen(false))
    fireEvent.keyDown(window, { key: '?', shiftKey: true })
    expect(useStore.getState().helpOpen).toBe(true)
  })

  it('ne se déclenche pas dans un champ de saisie', () => {
    renderHook(() => useGlobalShortcuts())
    const input = document.createElement('input')
    document.body.appendChild(input)
    fireEvent.keyDown(input, { key: '2' })
    expect(useStore.getState().view).toBe('compte')
    input.remove()
  })

  it('laisse passer les raccourcis du navigateur', () => {
    renderHook(() => useGlobalShortcuts())
    const ev = new KeyboardEvent('keydown', { key: '2', ctrlKey: true, cancelable: true, bubbles: true })
    window.dispatchEvent(ev)
    expect(ev.defaultPrevented).toBe(false)
    expect(useStore.getState().view).toBe('compte')
  })

  it('ignore la répétition automatique', () => {
    renderHook(() => useGlobalShortcuts())
    fireEvent.keyDown(window, { key: 's' })
    fireEvent.keyDown(window, { key: 's', repeat: true })
    expect(useStore.getState().space).toBe('liquid')
  })

  it('annule « g » quand une autre touche arrive', () => {
    renderHook(() => useGlobalShortcuts())
    fireEvent.keyDown(window, { key: 'g' })
    fireEvent.keyDown(window, { key: 'Shift' })
    fireEvent.keyDown(window, { key: 'c' })
    expect(useStore.getState().view).toBe('compte')
  })

  it('est désactivable', () => {
    localStorage.setItem('cryptobot.shortcuts.enabled', 'off')
    renderHook(() => useGlobalShortcuts())
    fireEvent.keyDown(window, { key: '3' })
    expect(useStore.getState().view).toBe('compte')
  })
})
