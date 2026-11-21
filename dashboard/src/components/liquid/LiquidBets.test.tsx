import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { LiquidBets } from './LiquidBets'
import type { PredictionArm, PredictionBet, PredictionSummary } from '../../lib/predictionTypes'

function bet(id: string, title: string, over: Partial<PredictionBet> = {}): PredictionBet {
  return {
    id, arm: 'alpha', coin: `#${id}`, side_name: 'Yes', title, domain: 'crypto_seuil',
    entry_time: `2026-11-1${id}T10:00:00Z`, entry_price: 0.4, stake_usd: 10,
    prob_estimee: 0.5, source_estimation: 'modele', expiry: null,
    status: 'regle', settle_time: '2026-11-20T00:00:00Z', settle_value: 1, pnl_net: 3, won: true,
    exemple_fictif: false, ...over,
  }
}

const ARM = {
  arm: 'alpha', role: 'regle', famille: 'prix', parent: null, variable: null,
  cree_depuis_le_site: false, variables_forkables: [], reglages: {}, enabled: true,
  domaines: [], description: '', ouverts: 1,
  stats: {
    paris_regles: 2, paris_indetermines: 0, gagnes: 1, win_rate: 0.5, profit_factor: 1.2,
    pnl_net: 1, drawdown_max_pct: 2, pires_5pct_moyenne: null, brier_bras: null,
    brier_marche: null, verdict: 'trop tôt', calibration: [], par_domaine: {},
  },
} as unknown as PredictionArm

const SUMMARY = {
  exemple_fictif: false, mode: 'papier', runner_online: true, updated_at_iso: null,
  marches_par_domaine: {},
  config: { capital_fictif_usd: 1000, mise_pct: 1, exposition_max_pct_par_domaine: 20, frais_pct: 0, frais_reglement_pct: 0, min_paris_verdict: 30 },
  arms: [ARM],
} as PredictionSummary

const BETS = [
  bet('1', 'BTC au-dessus de 100k', { pnl_net: 3 }),
  bet('2', 'Arsenal gagne', { pnl_net: -4, won: false, domain: 'sport_match' }),
  bet('3', 'ETH au-dessus de 5k', { pnl_net: null, status: 'ouvert', settle_time: undefined, settle_value: undefined }),
]

const titres = () => {
  const table = screen.getByRole('table', { name: 'Paris du bras alpha' })
  return within(table).getAllByRole('row').slice(1).map((r) => within(r).getAllByRole('cell')[1].textContent)
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(Response.json({ points: [] }))))
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('paris : explorer', () => {
  it('compteur N sur M, recherche, état vide avec la requête, réinitialiser', () => {
    render(<LiquidBets summary={SUMMARY} bets={BETS} demo={false} />)
    expect(screen.getByText('3 sur 3')).toBeTruthy()
    fireEvent.change(screen.getByRole('searchbox', { name: 'Chercher un pari' }), { target: { value: 'arsenal' } })
    expect(screen.getByText('1 sur 3')).toBeTruthy()
    expect(titres()).toEqual(['Arsenal gagne'])
    fireEvent.change(screen.getByRole('searchbox', { name: 'Chercher un pari' }), { target: { value: 'nope' } })
    expect(screen.getByText(/Aucun résultat pour « nope »/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Réinitialiser/ }))
    expect(titres()).toHaveLength(3)
  })

  it('tri par P&L net via l’en-tête, aria-sort suit, valeur absente en dernier', () => {
    render(<LiquidBets summary={SUMMARY} bets={BETS} demo={false} />)
    const th = within(screen.getByRole('table', { name: 'Paris du bras alpha' })).getByRole('columnheader', { name: /P&L net/ })
    fireEvent.click(within(th).getByRole('button'))
    expect(th.getAttribute('aria-sort')).toBe('ascending')
    expect(titres()).toEqual(['Arsenal gagne', 'BTC au-dessus de 100k', 'ETH au-dessus de 5k'])
    fireEvent.click(within(th).getByRole('button'))
    expect(th.getAttribute('aria-sort')).toBe('descending')
    expect(titres()).toEqual(['BTC au-dessus de 100k', 'Arsenal gagne', 'ETH au-dessus de 5k'])
  })

  it('Entrée ouvre le détail d’un pari ; Échap le ferme', async () => {
    render(<LiquidBets summary={SUMMARY} bets={BETS} demo={false} />)
    const table = screen.getByRole('table', { name: 'Paris du bras alpha' })
    const ligne = within(table).getAllByRole('row')[2]
    ligne.focus()
    fireEvent.keyDown(ligne, { key: 'Enter' })
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Prix payé')).toBeTruthy()
    fireEvent.keyDown(dialog, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(document.activeElement).toBe(ligne)
  })
})
