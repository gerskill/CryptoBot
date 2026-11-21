import type { Position } from '../../lib/types'
import { price, usd, pct, multiple, duration, compact, pnlColor } from '../../lib/format'
import { Fields } from './Fields'

/** `entry_time` est en secondes ; on tolère les millisecondes, jamais une date inventée. */
function entryDate(epoch: number): string | null {
  if (!Number.isFinite(epoch) || epoch <= 0) return null
  const date = new Date(epoch < 1e12 ? epoch * 1000 : epoch)
  return `${date.toISOString().slice(0, 19).replace('T', ' ')} UTC`
}

/** Contenu du tiroir d'une position : uniquement des champs déjà servis par l'API. */
export function PositionDetail({ position: p }: { position: Position }) {
  return (
    <>
      <Fields
        title="Résultat"
        rows={[
          ['Multiple', multiple(p.pnl_pct), pnlColor(p.pnl_pct)],
          ['P&L', pct(p.pnl_pct), pnlColor(p.pnl_pct)],
          ['P&L en dollars', `${p.pnl_usd >= 0 ? '+' : ''}${usd(p.pnl_usd, 2)}`, pnlColor(p.pnl_usd)],
          ['Pic depuis l’entrée', pct(p.high_water_pct)],
          ['Creux depuis l’entrée', pct(p.low_water_pct)],
        ]}
      />
      <Fields
        title="Prix et taille"
        rows={[
          ['Prix d’entrée', price(p.entry_price)],
          ['Prix actuel', p.current_price != null ? price(p.current_price) : null],
          ['Mise', usd(p.size_usd, 2)],
          ['Part restante', `${Math.round(p.remaining_fraction * 100)} %`],
        ]}
      />
      <Fields
        title="Règles de sortie"
        rows={[
          ['Stop loss', `${p.stop_loss_pct} %`],
          ['Take profit 1', `+${p.take_profit_1} %`],
          ['Take profit 2', p.take_profit_2 ? `+${p.take_profit_2} %` : null],
          ['Take profit 3', p.take_profit_3 ? `+${p.take_profit_3} %` : null],
          ['TP1 pris', p.tp1_hit ? 'oui' : null],
          ['TP2 pris', p.tp2_hit ? 'oui' : null],
          ['Stop remonté au point mort', p.breakeven_moved ? 'oui' : null],
          ['Trailing actif', p.trailing_active ? 'oui' : null],
          ['Durée maximale', `${p.max_hold_time_minutes} min`],
        ]}
      />
      <Fields
        title="À l’entrée"
        rows={[
          ['Stratégie', p.arm],
          ['Chaîne', p.chain],
          ['Ouverte le', entryDate(p.entry_time)],
          ['Durée écoulée', duration(p.duration_min)],
          ['Score alpha', p.alpha_score],
          ['Liquidité', p.liquidity_at_entry ? `$${compact(p.liquidity_at_entry)}` : null],
          ['Holders', p.holders_at_entry],
        ]}
      />
      <p className="text-[11px] leading-snug text-dim">
        La raison de sortie n’existe qu’à la clôture : elle apparaît ensuite au journal des trades.
      </p>
    </>
  )
}
