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
  /** Paris dont le P&L net est positif : un « gagné » acheté à 0,99 peut perdre après frais. */
  gagnes_net?: number
  win_rate_net?: number | null
  pertes_reelles?: number
  /** Issue illisible après le délai de prudence : comptée perdue. */
  regles_par_prudence?: number
}

/** Compte papier d'un bras (`paper.py::account`), comme un vrai compte. */
export type PredictionAccount = {
  capital: number
  valeur: number
  engage: number
  solde: number
  resultat_regle: number
  /** Valorisation au marché (`paper.py::mark_bet`) : absente avant la saison 3. */
  valeur_ouverte?: number
  /** Gain ou perte si les paris ouverts étaient revendus maintenant. Jamais mélangé au réalisé. */
  pnl_latent?: number
  /** Solde + valeur de revente des paris ouverts. */
  equite_marche?: number
  methodes_valorisation?: Record<string, number>
  valorise_le?: string | null
}

/** Baisse depuis le plus haut du compte, latent compris (`paper.py::equity_drawdown`). */
export type PredictionDrawdown = {
  drawdown_pic_pct: number | null
  drawdown_pic_usd: number | null
  plus_haut: number | null
}

/** Limites d'engagement d'un bras (`paper.py::engagement_limit`), base la plus prudente. */
export type PredictionEngagement = {
  base: number
  engage: number
  pct?: number
  limite?: number
  disponible?: number
  depasse?: boolean
  long_heures?: number
  long_pct?: number
  limite_long?: number
  engage_long?: number
  disponible_long?: number
  depasse_long?: boolean
}

/** Dernier passage de l'agent d'apprentissage (`learning.py::read_diagnostic`). */
export type LearningDiagnostic = {
  t: string | null
  paris_regles: number | null
  decision: string | null
  proposition: { variable: string; value: unknown; motif: string } | null
  fork_demande: string | null
}

export type PredictionArm = {
  arm: string
  role: 'temoin' | 'regle' | 'parieur' | 'consensus' | 'observateur'
  /** Famille d'analyse : transverse, prix, sport, questions, pourcentages. */
  famille: string
  /** Bras dont celui-ci est le fork, et LA variable changée. */
  parent: string | null
  variable: string | null
  /** Créé depuis le site (surcouche locale), pas dans le fichier versionné. */
  cree_depuis_le_site: boolean
  /** Variables qu'un fork de ce bras a le droit de changer (`overlay.py::VARIABLES`). */
  variables_forkables: string[]
  reglages: Record<string, number | string>
  enabled: boolean
  domaines: string[]
  description: string
  ouverts: number
  /** Absents d'une réponse antérieure à l'ADR 018. */
  compte?: PredictionAccount
  drawdown?: PredictionDrawdown
  engagement?: PredictionEngagement | null
  origine?: 'site' | 'apprentissage' | null
  apprentissage?: LearningDiagnostic | null
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
    mise_max_pct?: number | null
    ordre_min_usd?: number | null
    regle_taille_min?: string | null
    parts_entieres?: boolean | null
    delai_prudence_heures?: number | null
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
  shares?: number
  regle_par_prudence?: boolean
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

/** Demande déposée depuis le site (`overlay.py::request_status`). */
export type PredictionRequest = {
  id: string
  t: string
  action: 'toggle' | 'fork'
  arm?: string
  enabled?: boolean
  parent?: string
  name?: string
  variable?: string
  value?: unknown
  statut: 'en attente' | 'appliquée' | 'refusée'
  raison: string | null
}
