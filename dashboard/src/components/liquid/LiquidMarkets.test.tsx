import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { LiquidMarkets } from './LiquidMarkets'
import type { Catalogue, CatalogueMarket } from '../../lib/predictionTypes'

function marche(outcome: number, title: string, mid: number, over: Partial<CatalogueMarket> = {}): CatalogueMarket {
  return {
    outcome, coin: `#${outcome}`, title, group: null, label: null, domain: 'crypto_seuil',
    famille: 'prix', venue: null, question: null, underlying: 'BTC', target: null,
    expiry: `2026-12-0${outcome}T00:00:00Z`, start: null,
    sides: [{ name: 'Yes', coin: `#${outcome}`, mid }], ...over,
  }
}

const CATALOGUE: Catalogue = {
  exemple_fictif: false, updated_at_iso: '2026-11-20T10:00:00Z', total: 4,
  markets: [
    marche(1, 'BTC au-dessus de 100k', 0.8),
    marche(2, 'ETH au-dessus de 5k', 0.2),
    marche(3, 'SOL au-dessus de 300', 0.5),
    marche(4, 'Arsenal gagne', 0.6, { famille: 'sport', domain: 'sport_match' }),
  ],
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json(CATALOGUE))))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const titres = () =>
  within(screen.getByRole('table')).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[0].textContent)

describe('marchés Liquid : explorer', () => {
  it('compteur N sur M dans la famille affichée', async () => {
    render(<LiquidMarkets demo={false} />)
    await screen.findByText('3 sur 3')
  })

  it('recherche, état vide qui répète la requête, réinitialiser', async () => {
    render(<LiquidMarkets demo={false} />)
    await screen.findByText('3 sur 3')
    const champ = screen.getByRole('searchbox', { name: 'Chercher un marché' })
    fireEvent.change(champ, { target: { value: 'eth' } })
    expect(screen.getByText('1 sur 3')).toBeTruthy()
    fireEvent.change(champ, { target: { value: 'zzz' } })
    expect(screen.getByText(/Aucun résultat pour « zzz »/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Réinitialiser/ }))
    expect(screen.getByText('3 sur 3')).toBeTruthy()
  })

  it('tri par probabilité via l’en-tête avec aria-sort', async () => {
    render(<LiquidMarkets demo={false} />)
    await screen.findByText('3 sur 3')
    const th = screen.getByRole('columnheader', { name: /Probabilité/ })
    fireEvent.click(within(th).getByRole('button'))
    expect(th.getAttribute('aria-sort')).toBe('descending')
    expect(titres().map((t) => t?.slice(0, 3))).toEqual(['BTC', 'SOL', 'ETH'])
    fireEvent.click(within(th).getByRole('button'))
    expect(th.getAttribute('aria-sort')).toBe('ascending')
    expect(titres().map((t) => t?.slice(0, 3))).toEqual(['ETH', 'SOL', 'BTC'])
  })
})
