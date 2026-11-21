import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { TheArms } from './TheArms'
import { useStore } from '../lib/store'
import type { Arm, Stats } from '../lib/types'

function stats(pnl: number, trades: number, open: number): Stats {
  return {
    mode: 'paper', baseline: 1000, capital: 1000, equity: 1000 + pnl, total_pnl_usd: pnl,
    open_positions: open, total_trades: trades, win_rate: 40, profit_factor: 1.2,
    avg_win_pct: 30, avg_loss_pct: -10, max_drawdown_pct: 5, longest_drawdown: null,
    consecutive_losses: 0, cooldown_min: 0,
  }
}
function arm(name: string, pnl: number, trades: number, open: number, extra: Partial<Arm> = {}): Arm {
  return {
    name, role: 'voter', enabled: true, capital_pct: 10, entry_threshold: 60, min_confluence: 1,
    kept: 4, rejected: 6, qualified: 1, stats: stats(pnl, trades, open), ...extra,
  }
}

beforeEach(() => {
  useStore.setState({
    state: {
      bot_online: true,
      arms: [
        arm('sniper', 40, 20, 1, { description: 'entre tôt' }),
        arm('runner', -15, 8, 0),
        arm('consensus', 5, 3, 0, { role: 'consensus', min_confluence: 2 }),
      ],
    },
  })
})
afterEach(() => {
  cleanup()
  useStore.setState({ state: { bot_online: false }, panel: null })
})

const noms = () => screen.getAllByRole('button', { name: /^Détail de / }).map((b) => /^Détail de (sniper|runner|consensus)/.exec(b.textContent ?? '')?.[1])

describe('stratégies : explorer', () => {
  it('compteur, recherche et état vide qui répète la requête', () => {
    render(<TheArms />)
    expect(screen.getByText('3 sur 3')).toBeTruthy()
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'run' } })
    expect(screen.getByText('1 sur 3')).toBeTruthy()
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'xyz' } })
    expect(screen.getByText(/Aucun résultat pour « xyz »/)).toBeTruthy()
  })

  it('tri par P&L bascule asc/desc', () => {
    render(<TheArms />)
    fireEvent.click(screen.getByRole('button', { name: /P&L/ }))
    expect(noms()).toEqual(['sniper', 'consensus', 'runner'])
    fireEvent.click(screen.getByRole('button', { name: /P&L/ }))
    expect(noms()).toEqual(['runner', 'consensus', 'sniper'])
  })

  it('filtre « en position »', () => {
    render(<TheArms />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Statut' }), { target: { value: 'position' } })
    expect(screen.getByText('1 sur 3')).toBeTruthy()
    expect(noms()).toEqual(['sniper'])
  })

  it('le détail s’ouvre au clic et montre les règles du bras', () => {
    render(<TheArms />)
    fireEvent.click(screen.getByRole('button', { name: /^Détail de sniper/ }))
    const dialog = screen.getByRole('dialog', { name: 'sniper' })
    expect(within(dialog).getByText('entre tôt')).toBeTruthy()
    expect(within(dialog).getByText('Seuil alpha')).toBeTruthy()
  })

  it('panel (palette Cmd+K) ouvre le tiroir du bras ; fermer remet panel à null', async () => {
    useStore.setState({ panel: 'runner' })
    render(<TheArms />)
    const dialog = await screen.findByRole('dialog', { name: 'runner' })
    expect(dialog).toBeTruthy()
    fireEvent.keyDown(document.body, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(useStore.getState().panel).toBeNull()
  })

  it('un clic sur un bras écrit panel', () => {
    render(<TheArms />)
    fireEvent.click(screen.getByRole('button', { name: /^Détail de sniper/ }))
    expect(useStore.getState().panel).toBe('sniper')
  })
})
