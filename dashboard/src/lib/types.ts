/** Formes renvoyées par l'API. Miroir de `src/core/state.py`. */

export type Candidate = {
  token_address: string
  symbol: string
  name: string
  chain: string
  url: string | null
  price_usd: number
  liquidity_usd: number
  market_cap: number
  volume_1h: number
  age_hours: number
  price_change_5m: number
  price_change_1h: number
  buys_5m: number
  sells_5m: number
  holders: number | null
  holders_is_exact: boolean | null
  top_holder_pct: number | null
  top10_holder_pct: number | null
  dev_wallet_pct: number | null
  rugcheck_score: number | null
  rugcheck_risks: string[]
  lp_locked_pct: number | null
  social_mentions_1h: number | null
  social_unique_authors: number | null
  social_engagement: number | null
  social_velocity_15m: number | null
  /** Toujours null : GMGN n'a pas d'API publique. */
  smart_money_buys_30m: number | null
  alpha_score: number
  alpha_score_absolute: number
  sub_scores: Record<string, number>
  rejected_reason: string | null
}

export type Position = {
  id: string
  /** Stratégie propriétaire. Plusieurs bras peuvent tenir le même token. */
  arm?: string
  symbol: string
  token_address: string
  chain: string
  entry_price: number
  current_price: number | null
  size_usd: number
  pnl_pct: number
  pnl_usd: number
  multiple: number
  remaining_fraction: number
  duration_min: number
  high_water_pct: number
  low_water_pct: number
  alpha_score: number
  stop_loss_pct: number
  take_profit_1: number
  take_profit_2: number
  take_profit_3: number
  max_hold_time_minutes: number
  tp1_hit: boolean
  tp2_hit: boolean
  breakeven_moved: boolean
  trailing_active: boolean
  liquidity_at_entry: number
  holders_at_entry: number | null
  entry_time: number
}

export type Stats = {
  mode: string
  /** Mise de départ configurée : référence du P&L cumulé. */
  baseline: number
  capital: number
  equity: number
  total_pnl_usd: number
  open_positions: number
  total_trades: number
  win_rate: number
  profit_factor: number
  avg_win_pct: number
  avg_loss_pct: number
  max_drawdown_pct: number
  /**
   * Le plus LONG passage sous le plus haut, pas le plus profond — ce sont
   * rarement le même épisode. `null` = l'équité n'est jamais descendue sous
   * son pic, ce qui n'est pas « un épisode de durée nulle ».
   * `recovered: false` = drawdown encore en cours : la durée affichée est
   * celle observée à ce jour, pas une durée finale.
   */
  longest_drawdown: {
    depth_pct: number
    trades: number
    /** `null` quand les horodatages de sortie manquent au journal. */
    hours: number | null
    recovered: boolean
  } | null
  consecutive_losses: number
  cooldown_min: number
}

export type BotState = {
  bot_online: boolean
  reason?: string
  state_age_seconds?: number
  mode?: string
  paused?: boolean
  cycle?: number
  interval_seconds?: number
  next_scan_at?: number
  /** Durée du cycle PRÉCÉDENT (mesure complète : monitoring, scan, entrées,
   *  notifications). Décalée d'un cycle par construction — voir main.py. */
  cycle_duration_sec?: number
  stats?: Stats
  positions?: Position[]
  candidates?: Candidate[]
  rejected?: Candidate[]
  scan?: { scanned: number; cached_skipped: number; duration_sec: number }
  params?: Record<string, any>
  shadow?: { tracked: number; judged: number }
  shadow_by_family?: Record<string, { sample: number; missed_rate: number; avg_peak_gain_pct: number }>
  entry_threshold?: number
  live_allowed?: [boolean, string]
  api_status?: Record<string, boolean>
  arms?: Arm[]
  confluence?: ConfluenceRow[]
  aggregate?: Aggregate
  capabilities?: CapabilityStatus[]
  blind_spots?: string[]
  updated_at?: number
}

/** Une stratégie : ses règles, son portefeuille, ce qu'elle a vu ce cycle. */
export type Arm = {
  name: string
  role: 'voter' | 'consensus' | 'test'
  enabled: boolean
  capital_pct: number
  description?: string
  /** Bras en pause (2026-11-23) : plus d'entrée, positions jusqu'à leur sortie. */
  paused?: boolean
  /** Rôle lisible : famille d'entrée, sortie testée, témoin… */
  role_label?: string
  /** Famille d'entrée testée (bras `entree_*`). */
  famille?: string | null
  /** false = réglages figés (apprentissage coupé). */
  learning?: boolean
  /** Clés présentes dans la config mais lues par aucun code. */
  reglages_non_lus?: string[]
  entry_threshold: number
  min_confluence: number
  stop_loss_pct?: number
  take_profit_1?: number
  max_hold_time_minutes?: number
  kept?: number
  rejected?: number
  qualified?: number
  social_served?: number
  trades?: number
  stats?: Stats
}

export type ConfluenceRow = {
  token_address: string
  symbol: string
  accepted_by: string[]
  above_threshold_by: string[]
  accepted_count: number
  above_threshold_count: number
  scores_by_arm: Record<string, number>
}

export type Trade = {
  /** Stratégie propriétaire. Sans ça, impossible de comparer les bras. */
  arm?: string
  id: string
  token: string
  exit_reason: string
  pnl_pct: number
  pnl_usd: number
  duration_min: number
  score_alpha: number
  entry_price: number
  exit_price: number
  liquidity_at_entry: number
  holders_at_entry: number | null
  age_hours_at_entry: number
  timestamp_exit: string
  /** Plus haut/bas latents pendant la détention — déjà mesurés au journal,
   *  c'est ce qui distingue un stop loss « normal » d'un pic manqué. */
  peak_pct?: number
  trough_pct?: number
}

/** Vue d'ensemble des stratégies. `stats` reste le témoin seul. */
export type Aggregate = {
  arms: number
  open_positions: number
  exposure_usd: number
  total_trades: number
  total_pnl_usd: number
  equity: number
  arms_trading: number
  /** Performance de la FLOTTE, recalculée sur les journaux de tous les bras.
   *  `stats` ne décrit que le témoin, qui est gelé : s'y fier affichait
   *  « 4 gagnants sur 39 » alors que la flotte en comptait 27 sur 93. */
  wins: number
  closed_trades: number
  win_rate: number
  profit_factor: number
  /** Le drawdown ne s'additionne pas entre portefeuilles : on rend le pire. */
  worst_arm_drawdown_pct: number
}

/** Santé par capacité, pas par API : « peut-on encore avoir des bougies ». */
export type CapabilityStatus = {
  capability: string
  using: string | null
  degraded: boolean
  available: string[]
  down: string[]
  blind: boolean
}

/** Fenêtres servies par `/api/arms/performance` — liste fermée côté serveur. */
export type PerformanceWindow = '7d' | '30d' | 'all'

export type IntervalDict = {
  value: number
  low: number
  high: number
  n: number
  conclusive: boolean
}

/** Miroir de `src/core/stats.py::window_performance` + `arm`, `role`, `verdict`. */
export type ArmPerformance = {
  arm: string
  role?: string | null
  trades: number
  wins: number
  win_rate: number
  win_rate_interval: IntervalDict | null
  pnl_usd: number
  gross_win: number
  gross_loss: number
  /** `null` = aucune perte dans la fenêtre (l'infini ne passe pas en JSON). */
  profit_factor: number | null
  exit_cost_coverage_pct: number
  rug_pulls: number
  manual_closes: number
  equity_start: number
  equity_end: number
  max_drawdown_pct: number
  daily: { date: string; trades: number; pnl_usd: number }[]
  /** `null` pour le témoin : il ne se compare pas à lui-même. */
  verdict: { verdict: string; interval: IntervalDict | null; reference: IntervalDict | null } | null
}

export type ArmsPerformanceResponse = {
  window: PerformanceWindow
  since: string | null
  until: string
  starting_capital_per_arm: number
  baseline: string
  arms: ArmPerformance[]
  fleet: {
    arm: 'flotte'
    label: string
    trades: number
    wins: number
    win_rate: number
    pnl_usd: number
    profit_factor: number | null
  }
}
