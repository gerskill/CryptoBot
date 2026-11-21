import type { Position } from './types'

// Au-delà de cette fraction de la distance entrée -> SL déjà parcourue, la
// position est en danger réel : bordure pulsante. Symétrique côté TP1 avec
// PROXIMITE_TP1 — « à moins de 30 % du TP1 » = 70 % de la distance couverte.
export const DANGER_SL_RATIO = 0.5
export const PROXIMITE_TP1_RATIO = 0.7

type Signal = Pick<Position, 'pnl_pct' | 'stop_loss_pct' | 'take_profit_1'>

/**
 * `stop_loss_pct` est DÉJÀ `effective_stop_loss_pct` côté API (tampon de
 * glissement inclus) : l'alerte se déclenche sur le seuil RÉEL, pas sur la
 * règle nominale. Source unique : bordure `pulse-danger` ET alertes navigateur.
 */
export function isNearStop(position: Signal): boolean {
  return position.pnl_pct <= position.stop_loss_pct * DANGER_SL_RATIO
}

export function isNearTp1(position: Signal): boolean {
  return !isNearStop(position) && position.pnl_pct >= position.take_profit_1 * PROXIMITE_TP1_RATIO
}
