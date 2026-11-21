import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ActivePositions } from './ActivePositions'
import { useStore } from '../lib/store'
import type { Position } from '../lib/types'

function position(id: string, symbol: string, arm: string, pnl: number): Position {
  return {
    id, symbol, arm, token_address: `addr-${id}`, chain: 'solana',
    entry_price: 0.001, current_price: 0.0012, size_usd: 50,
    pnl_pct: pnl, pnl_usd: pnl / 2, multiple: 1 + pnl / 100,
    remaining_fraction: 1, duration_min: 12, high_water_pct: 20, low_water_pct: -3,
    alpha_score: 71, stop_loss_pct: -30, take_profit_1: 50, take_profit_2: 100, take_profit_3: 200,
    max_hold_time_minutes: 60, tp1_hit: false, tp2_hit: false, breakeven_moved: false,
    trailing_active: false, liquidity_at_entry: 40000, holders_at_entry: null,
    entry_time: 1_760_000_000,
  }
}

function seed(positions: Position[]) {
  useStore.setState({ state: { bot_online: true, positions } })
}

const symboles = () => screen.getAllByRole('article').map((a) => within(a).getAllByText(/^(PEPE|DOGE|WIF)$/)[0].textContent)

beforeEach(() => {
  seed([position('1', 'PEPE', 'sniper', 5), position('2', 'DOGE', 'runner', -12), position('3', 'WIF', 'sniper', 30)])
})
afterEach(() => {
  cleanup()
  useStore.setState({ state: { bot_online: false } })
})

describe('positions ouvertes : explorer', () => {
  it('compteur N sur M et filtre par recherche (symbole ou bras)', async () => {
    render(<ActivePositions />)
    expect(screen.getByText('3 sur 3')).toBeTruthy()
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'runner' } })
    expect(screen.getByText('1 sur 3')).toBeTruthy()
    // les cartes sortantes finissent leur animation de sortie
    await waitFor(() => expect(symboles()).toEqual(['DOGE']))
  })

  it('aucun résultat : message qui répète la requête, puis réinitialiser', () => {
    render(<ActivePositions />)
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'zzz' } })
    expect(screen.getByText(/Aucun résultat pour « zzz »/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Réinitialiser/ }))
    expect(screen.getByText('3 sur 3')).toBeTruthy()
    expect(symboles()).toHaveLength(3)
  })

  it('tri par P&L : le plus gros d’abord, puis bascule', () => {
    render(<ActivePositions />)
    fireEvent.click(screen.getByRole('button', { name: /P&L/ }))
    expect(symboles()).toEqual(['WIF', 'PEPE', 'DOGE'])
    fireEvent.click(screen.getByRole('button', { name: /P&L/ }))
    expect(symboles()).toEqual(['DOGE', 'PEPE', 'WIF'])
  })

  it('filtre par bras', async () => {
    render(<ActivePositions />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Bras' }), { target: { value: 'sniper' } })
    expect(screen.getByText('2 sur 3')).toBeTruthy()
    await waitFor(() => expect(symboles()).toEqual(['PEPE', 'WIF']))
  })

  it('un clic ouvre le détail, sans inventer les champs absents', () => {
    render(<ActivePositions />)
    // vrai bouton (pas d'aria-label sur l'article, qui masquerait son contenu)
    expect(screen.getAllByRole('article')[0].getAttribute('aria-label')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Détail de PEPE/ }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByText('Prix d’entrée')).toBeTruthy()
    // holders_at_entry est null : aucune ligne « Holders »
    expect(within(dialog).queryByText(/Holders/)).toBeNull()
  })

  it('position clôturée pendant le détail : tiroir fermé, avis, focus sur la recherche, pas de réouverture', async () => {
    render(<ActivePositions />)
    fireEvent.click(screen.getByRole('button', { name: /Détail de PEPE/ }))
    await screen.findByRole('dialog')
    act(() => seed([position('2', 'DOGE', 'runner', -12), position('3', 'WIF', 'sniper', 30)]))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(screen.getByRole('status').textContent).toMatch(/PEPE.*clôturée/)
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('searchbox')))
    // l'id réapparaît : rien ne se rouvre tout seul
    act(() => seed([position('1', 'PEPE', 'sniper', 5), position('2', 'DOGE', 'runner', -12)]))
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('l’ordre est figé pendant le survol, rétabli ensuite', async () => {
    render(<ActivePositions />)
    fireEvent.click(screen.getByRole('button', { name: /P&L/ })) // desc : WIF, PEPE, DOGE
    await waitFor(() => expect(symboles()).toEqual(['WIF', 'PEPE', 'DOGE']))
    const liste = screen.getByTestId('positions-list')
    fireEvent.mouseEnter(liste)
    act(() => seed([position('1', 'PEPE', 'sniper', 99), position('2', 'DOGE', 'runner', -12), position('3', 'WIF', 'sniper', 30)]))
    await waitFor(() => expect(symboles()).toEqual(['WIF', 'PEPE', 'DOGE']))
    fireEvent.mouseLeave(liste)
    await waitFor(() => expect(symboles()).toEqual(['PEPE', 'WIF', 'DOGE']))
  })
})
