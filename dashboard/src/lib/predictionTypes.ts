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
  /** Part gagnée par tranche de prix payé. `null` = aucun pari dans la tranche. */
  calibration: {
    tranche: string
    paris: number
    prix_moyen: number | null
    part_gagnee: number | null
    pnl_net: number
  }[]
  par_domaine: Record<string, { paris: number; gagnes: number; pnl_net: number }>
}

export type PredictionArm = {
  arm: string
  role: 'temoin' | 'regle' | 'parieur' | 'consensus' | 'observateur'
  /** Famille d'analyse : transverse, prix, sport, questions, pourcentages. */
  famille: string
  /** Bras dont celui-ci est le fork, et LA variable changée. */
  parent: string | null
  variable: string | null
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
  famille?: string
  venue?: string | null
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

/** Un marché de prédiction vu au dernier cycle (`runner.py::_catalogue`). */
export type CatalogueMarket = {
  outcome: number
  coin: string
  title: string
  group: string | null
  label: string | null
  domain: string
  famille: string
  venue: string | null
  question: string | null
  underlying: string | null
  target: number | null
  expiry: string | null
  start: string | null
  sides: { name: string; coin: string; mid: number | null }[]
}

export type Catalogue = {
  exemple_fictif: boolean
  updated_at_iso: string | null
  total: number
  markets: CatalogueMarket[]
}

/** Un marché à terme relevé (`hl_api.py::perp_markets`). Affichage seul. */
export type PerpMarket = {
  coin: string
  symbol: string
  dex: string
  mark: number
  prev_day: number | null
  change_24h_pct: number | null
  volume_24h_usd: number | null
  funding_8h_pct: number | null
  open_interest_usd: number | null
  max_leverage: number | null
}

export type Perps = { updated_at_iso: string | null; total: number; markets: PerpMarket[] }

export type CoherenceRow = {
  t: string
  arm: string
  type: 'somme_sous_100' | 'somme_sur_100' | 'echelle_non_monotone' | 'ecart_entre_venues'
  domain?: string
  libelle?: string | null
  somme_milieux?: number
  issues?: number
  sous_jacent?: string | null
  echeance?: string | null
  seuil_bas?: number
  seuil_haut?: number
  seuil?: number
  prix_bas?: number
  prix_haut?: number
  venue?: string | null
  venue_basse?: string
  venue_haute?: string
}
