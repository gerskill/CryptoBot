import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { CommandPalette } from './CommandPalette'
import { useStore } from '../lib/store'

function ouvrir() {
  act(() => useStore.getState().setPaletteOpen(true))
}

describe('CommandPalette', () => {
  beforeEach(() => {
    localStorage.clear()
    useStore.setState({ paletteOpen: false, view: 'dashboard', space: 'memecoins', announcement: '' })
  })
  afterEach(cleanup)

  it('est fermée par défaut', () => {
    render(<CommandPalette />)
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('expose un dialogue avec un combobox relié à une listbox', () => {
    render(<CommandPalette />)
    ouvrir()
    const dialog = screen.getByRole('dialog', { name: /palette/i })
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    const input = screen.getByRole('combobox')
    const list = screen.getByRole('listbox')
    expect(input.getAttribute('aria-controls')).toBe(list.id)
    expect(document.activeElement).toBe(input)
    const active = input.getAttribute('aria-activedescendant')
    expect(document.getElementById(active ?? '')?.getAttribute('aria-selected')).toBe('true')
  })

  it('filtre en flou et navigue avec Entrée', () => {
    render(<CommandPalette />)
    ouvrir()
    const input = screen.getByRole('combobox')
    fireEvent.change(input, { target: { value: 'wkly' } })
    expect(screen.getAllByRole('option')[0].textContent).toContain('Weekly')
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(useStore.getState().view).toBe('weekly')
    expect(useStore.getState().paletteOpen).toBe(false)
    expect(useStore.getState().announcement).toContain('Weekly')
  })

  it('se déplace avec les flèches (circulaire)', () => {
    render(<CommandPalette />)
    ouvrir()
    const input = screen.getByRole('combobox')
    const first = input.getAttribute('aria-activedescendant')
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(input.getAttribute('aria-activedescendant')).not.toBe(first)
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(input.getAttribute('aria-activedescendant')).toBe(first)
    fireEvent.keyDown(input, { key: 'ArrowUp' })
    expect(input.getAttribute('aria-activedescendant')).not.toBe(first)
  })

  it('se ferme avec Échap et rend le focus', () => {
    const bouton = document.createElement('button')
    document.body.appendChild(bouton)
    bouton.focus()
    render(<CommandPalette />)
    ouvrir()
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' })
    expect(useStore.getState().paletteOpen).toBe(false)
    expect(document.activeElement).toBe(bouton)
    bouton.remove()
  })

  it('garde le focus dans le dialogue avec Tab', () => {
    render(<CommandPalette />)
    ouvrir()
    const input = screen.getByRole('combobox')
    const fermer = screen.getByRole('button', { name: /fermer/i })
    fermer.focus()
    fireEvent.keyDown(fermer, { key: 'Tab' })
    expect(document.activeElement).toBe(input)
    fireEvent.keyDown(input, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(fermer)
  })

  it('liste les récents en premier', () => {
    localStorage.setItem('cryptobot.palette.recents', JSON.stringify(['tab:config']))
    render(<CommandPalette />)
    ouvrir()
    expect(screen.getAllByRole('option')[0].textContent).toContain('Config')
  })

  it('annonce l’absence de résultat', () => {
    render(<CommandPalette />)
    ouvrir()
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'zzzzqq' } })
    expect(screen.getAllByText(/aucun résultat/i).length).toBeGreaterThan(0)
  })
})
