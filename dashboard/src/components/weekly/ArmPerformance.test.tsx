import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { ArmPerformance } from './ArmPerformance'
import type { ArmPerformance as ArmPerf, ArmsPerformanceResponse } from '../../lib/types'

function bras(arm: string, pnl: number, verdict: ArmPerf['verdict']): ArmPerf {
  return {
    arm,
    trades: 10,
    wins: 6,
    win_rate: 60,
    win_rate_interval: { value: 60, low: 31, high: 83, n: 10, conclusive: false },
    pnl_usd: pnl,
    gross_win: 120,
    gross_loss: 20,
    profit_factor: 6,
    exit_cost_coverage_pct: 100,
    rug_pulls: 0,
    manual_closes: 0,
    equity_start: 1000,
    equity_end: 1000 + pnl,
    max_drawdown_pct: 4.2,
    daily: [],
    verdict,
  }
}

function reponse(arms: ArmPerf[], fenetre: ArmsPerformanceResponse['window'] = '7d'): ArmsPerformanceResponse {
  return {
    window: fenetre,
    since: null,
    until: '2026-11-17T00:00:00Z',
    starting_capital_per_arm: 1000,
    baseline: 'baseline',
    arms,
    fleet: {
      arm: 'flotte',
      label: 'somme des bras, non agrégeable',
      trades: arms.reduce((t, a) => t + a.trades, 0),
      wins: arms.reduce((t, a) => t + a.wins, 0),
      win_rate: 60,
      pnl_usd: arms.reduce((t, a) => t + a.pnl_usd, 0),
      profit_factor: 6,
    },
  }
}

const DEUX_BRAS = reponse([
  bras('sniper', 100, { verdict: 'meilleur', interval: null, reference: null }),
  bras('baseline', 50, null),
])

function reponseOk(corps: ArmsPerformanceResponse) {
  return Promise.resolve(Response.json(corps))
}

/** Lignes du tableau, hors en-tête. */
function lignes(): HTMLElement[] {
  return screen.getAllByRole('row').slice(1)
}

function premiereCellule(ligne: HTMLElement): string {
  return within(ligne).getAllByRole('cell')[0].textContent ?? ''
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(() => reponseOk(DEUX_BRAS)))
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('panneau P&L par bras', () => {
  it('chaque ligne de bras affiche son bras', async () => {
    render(<ArmPerformance />)
    await screen.findByText('sniper')

    const rangees = lignes()
    // deux bras + la ligne flotte
    expect(rangees).toHaveLength(3)
    expect(premiereCellule(rangees[0])).toBe('sniper')
    expect(premiereCellule(rangees[1])).toBe('baseline')
  })

  it("la somme s'affiche flotte (somme), jamais sous un nom de bras", async () => {
    render(<ArmPerformance />)
    await screen.findByText('sniper')

    const rangees = lignes()
    const derniere = rangees[rangees.length - 1]
    expect(premiereCellule(derniere)).toBe('flotte (somme)')
    // aucune ligne de bras ne porte le libellé de la somme, et la somme ne
    // reprend le nom d'aucun bras
    for (const ligne of rangees.slice(0, -1)) {
      expect(premiereCellule(ligne)).not.toContain('flotte')
    }
    expect(['sniper', 'baseline']).not.toContain(premiereCellule(derniere))
  })

  it('cliquer 30 j redemande la fenêtre 30d', async () => {
    const fetchMock = vi.mocked(fetch)
    render(<ArmPerformance />)
    await screen.findByText('sniper')
    expect(String(fetchMock.mock.calls[0][0])).toContain('window=7d')

    fetchMock.mockImplementation(() => reponseOk({ ...DEUX_BRAS, window: '30d' }))
    fireEvent.click(screen.getByRole('tab', { name: '30 j' }))

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((appel) => String(appel[0]))
      expect(urls.some((u) => u.includes('window=30d'))).toBe(true)
    })
  })

  it('une erreur HTTP affiche Erreur de chargement et aucune ligne chiffrée', async () => {
    vi.mocked(fetch).mockImplementation(() =>
      Promise.resolve(new Response('{}', { status: 500 })),
    )
    render(<ArmPerformance />)

    expect(await screen.findByText(/Erreur de chargement/)).toBeTruthy()
    expect(screen.getByText(/HTTP 500/)).toBeTruthy()
    // ni tableau, ni ligne de bras, ni ligne flotte
    expect(screen.queryAllByRole('row')).toHaveLength(0)
    expect(screen.queryByText('flotte (somme)')).toBeNull()
    expect(screen.queryByText('sniper')).toBeNull()
  })

  it('sans aucun bras, seule la ligne flotte s\'affiche sans planter', async () => {
    vi.mocked(fetch).mockImplementation(() => reponseOk(reponse([])))
    render(<ArmPerformance />)
    await screen.findByText('flotte (somme)')

    const rangees = lignes()
    expect(rangees).toHaveLength(1)
    expect(premiereCellule(rangees[0])).toBe('flotte (somme)')
  })
})
