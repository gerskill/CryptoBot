import type { Arm, Position, Trade } from './types'
import type { CsvColumn } from './csv'

/** Colonnes des trois exports. Ce qui est exporté = ce qui est affiché. */
export const TRADE_COLUMNS: CsvColumn<Trade>[] = [
  { header: 'sortie_utc', value: (t) => t.timestamp_exit },
  { header: 'bras', value: (t) => t.arm },
  { header: 'token', value: (t) => t.token },
  { header: 'raison_sortie', value: (t) => t.exit_reason },
  { header: 'pnl_pct', value: (t) => t.pnl_pct },
  { header: 'pnl_usd', value: (t) => t.pnl_usd },
  { header: 'duree_min', value: (t) => t.duration_min },
  { header: 'score_alpha', value: (t) => t.score_alpha },
  { header: 'prix_entree', value: (t) => t.entry_price },
  { header: 'prix_sortie', value: (t) => t.exit_price },
]

export const POSITION_COLUMNS: CsvColumn<Position>[] = [
  { header: 'bras', value: (p) => p.arm },
  { header: 'symbole', value: (p) => p.symbol },
  { header: 'prix_entree', value: (p) => p.entry_price },
  { header: 'prix_actuel', value: (p) => p.current_price },
  { header: 'pnl_pct', value: (p) => p.pnl_pct },
  { header: 'pnl_usd', value: (p) => p.pnl_usd },
  { header: 'mise_usd', value: (p) => p.size_usd },
  { header: 'stop_loss_pct', value: (p) => p.stop_loss_pct },
  { header: 'take_profit_1_pct', value: (p) => p.take_profit_1 },
  { header: 'duree_min', value: (p) => p.duration_min },
  { header: 'fraction_restante', value: (p) => p.remaining_fraction },
]

export const ARM_COLUMNS: CsvColumn<Arm>[] = [
  { header: 'bras', value: (a) => a.name },
  { header: 'role', value: (a) => a.role },
  { header: 'actif', value: (a) => a.enabled },
  { header: 'capital_pct', value: (a) => a.capital_pct },
  { header: 'equite_usd', value: (a) => a.stats?.equity },
  { header: 'pnl_usd', value: (a) => a.stats?.total_pnl_usd },
  { header: 'trades', value: (a) => a.stats?.total_trades },
  { header: 'taux_gain_pct', value: (a) => a.stats?.win_rate },
  { header: 'profit_factor', value: (a) => a.stats?.profit_factor },
  { header: 'drawdown_max_pct', value: (a) => a.stats?.max_drawdown_pct },
]
