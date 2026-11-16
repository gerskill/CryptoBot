/**
 * Marchés de prédiction Liquid (PAPIER) — miroir de `src/prediction/` côté
 * Python (`paper.py::compute_stats`, `runner.py::_open`, `api/server.py`).
 * Un champ ajouté d'un seul côté crée une divergence silencieuse.
 */

export type PredictionStats = {
  paris_regles: number
  paris_indetermines: number
  gagnes: number
  /** null = aucun pari réglé. Jamais un 0 % affiché à la place d'une absence. */
  win_rate: number | null
  profit_factor: number | null
  pnl_net: number
  drawdown_max_pct: number | null
  pires_5pct_moyenne: number | null
  brier_bras: number | null
  brier_marche: number | null
  verdict: string
}

export type PredictionArm = {
  arm: string
  role: 'temoin' | 'parieur' | 'consensus' | 'observateur'
  enabled: boolean
  domaines: string[]
  description: string
  ouverts: number
  stats: PredictionStats
}

export type PredictionSummary = {
  exemple_fictif: boolean
  mode: string
  runner_online: boolean
  updated_at_iso: string | null
  marches_par_domaine: Record<string, number>
  config: {
    capital_fictif_usd: number
    mise_pct: number
    exposition_max_pct_par_domaine: number
    frais_pct: number
    frais_reglement_pct: number
    min_paris_verdict: number
  }
  arms: PredictionArm[]
}

export type PredictionBet = {
  id: string
  arm: string
  coin: string
  side_name: string
  title: string
  domain: string
  /** Instant ISO UTC de l'entrée simulée — le même que sur le graphique. */
  entry_time: string
  /** Prix moyen payé au carnet vendeur, entre 0 et 1. */
  entry_price: number
  stake_usd: number
  prob_estimee: number | null
  source_estimation: string
  expiry: string | null
  status: 'ouvert' | 'regle' | 'indetermine'
  settle_time?: string
  settle_value?: number | null
  pnl_net?: number | null
  won?: boolean | null
  exemple_fictif: boolean
}

export type PricePoint = { t: string; mid: number }
