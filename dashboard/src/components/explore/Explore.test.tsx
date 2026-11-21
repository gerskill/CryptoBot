import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SortableTh, SortBar } from './SortControls'
import { ExploreBar, NoResults } from './ExploreBar'

afterEach(cleanup)

describe('SortableTh', () => {
  it('porte aria-sort et déclenche le tri au clic', () => {
    const onSort = vi.fn()
    render(
      <table><thead><tr>
        <SortableTh label="P&L" sortKey="pnl" ariaSort="ascending" onSort={onSort} />
      </tr></thead></table>,
    )
    const th = screen.getByRole('columnheader')
    expect(th.getAttribute('aria-sort')).toBe('ascending')
    fireEvent.click(screen.getByRole('button', { name: /P&L/ }))
    expect(onSort).toHaveBeenCalledWith('pnl')
  })
})

describe('SortBar', () => {
  it('marque la clé active et la direction', () => {
    const onSort = vi.fn()
    render(
      <SortBar
        options={[{ key: 'pnl', label: 'P&L' }, { key: 'nom', label: 'Nom' }]}
        sort={{ key: 'pnl', dir: 'desc' }}
        onSort={onSort}
      />,
    )
    const actif = screen.getByRole('button', { name: /P&L/ })
    expect(actif.getAttribute('aria-pressed')).toBe('true')
    expect(actif.textContent).toContain('↓')
    expect(screen.getByRole('button', { name: /Nom/ }).getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(screen.getByRole('button', { name: /Nom/ }))
    expect(onSort).toHaveBeenCalledWith('nom')
  })
})

describe('ExploreBar', () => {
  const base = { label: 'Chercher', query: '', onQuery: () => {}, shown: 3, total: 12, isFiltered: false, onReset: () => {} }

  it('affiche le compteur « N sur M »', () => {
    render(<ExploreBar {...base} />)
    expect(screen.getByText('3 sur 12')).toBeTruthy()
  })

  it('saisie remontée à onQuery', () => {
    const onQuery = vi.fn()
    render(<ExploreBar {...base} onQuery={onQuery} />)
    fireEvent.change(screen.getByRole('searchbox', { name: 'Chercher' }), { target: { value: 'pepe' } })
    expect(onQuery).toHaveBeenCalledWith('pepe')
  })

  it('le bouton réinitialiser n’apparaît que si la liste est filtrée', () => {
    const onReset = vi.fn()
    const { rerender } = render(<ExploreBar {...base} onReset={onReset} />)
    expect(screen.queryByRole('button', { name: /Réinitialiser/ })).toBeNull()
    rerender(<ExploreBar {...base} isFiltered onReset={onReset} />)
    fireEvent.click(screen.getByRole('button', { name: /Réinitialiser/ }))
    expect(onReset).toHaveBeenCalled()
  })
})

describe('NoResults', () => {
  it('répète la requête', () => {
    render(<NoResults query="doge" />)
    expect(screen.getByText(/Aucun résultat pour/).textContent).toContain('« doge »')
  })
  it('sans requête (filtre seul) : message sans guillemets vides', () => {
    render(<NoResults query="" />)
    expect(screen.getByText(/Aucun résultat/).textContent).not.toContain('«')
  })
})
