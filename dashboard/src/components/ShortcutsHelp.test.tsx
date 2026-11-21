import { afterEach, describe, expect, it } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ShortcutsHelp } from './ShortcutsHelp'
import { useStore } from '../lib/store'

describe('ShortcutsHelp', () => {
  afterEach(cleanup)

  it('rend le focus au déclencheur à la fermeture', () => {
    const declencheur = document.createElement('button')
    document.body.appendChild(declencheur)
    declencheur.focus()
    render(<ShortcutsHelp />)
    act(() => useStore.getState().setHelpOpen(true))
    expect(document.activeElement).toBe(screen.getByRole('button', { name: /fermer/i }))
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
    expect(document.activeElement).toBe(declencheur)
    declencheur.remove()
  })
})
