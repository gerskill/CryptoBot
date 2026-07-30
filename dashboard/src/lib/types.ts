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
  updated_at?: number
}

export type Trade = {
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
}
