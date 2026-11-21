import type { Accessors } from '../../lib/explore'
import type { PredictionBet } from '../../lib/predictionTypes'

/** Libellé d'issue : « gagné / perdu » pour un pari réglé, sinon son statut. */
export const issueLabel = (bet: PredictionBet): string =>
  bet.status === 'regle' ? (bet.won ? 'gagné' : 'perdu') : bet.status === 'ouvert' ? 'ouvert' : 'indéterminé'

export const BET_ACCESSORS: Accessors<PredictionBet> = {
  entree: (b) => b.entry_time,
  marche: (b) => b.title,
  cote: (b) => b.side_name,
  paye: (b) => b.entry_price,
  estime: (b) => b.prob_estimee,
  issue: (b) => issueLabel(b),
  pnl: (b) => b.pnl_net,
}

