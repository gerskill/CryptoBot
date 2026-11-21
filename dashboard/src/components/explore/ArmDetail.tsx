import type { Arm } from '../../lib/types'
import { usd, pct, pnlColor } from '../../lib/format'
import { Fields } from './Fields'

/** Profit factor : arrondi, et « ∞ » quand il n'y a aucune perte (jamais « Infinity »). */
const factor = (value: number): string | null =>
  Number.isNaN(value) ? null : Number.isFinite(value) ? value.toFixed(2) : '∞'

/** Contenu du tiroir d'une stratégie : règles, entonnoir du cycle, résultats. */
export function ArmDetail({ arm }: { arm: Arm }) {
  const s = arm.stats
  const trades = s?.total_trades ?? arm.trades ?? 0
  const seen = (arm.kept ?? 0) + (arm.rejected ?? 0)
  return (
    <>
      {arm.description && <p className="text-xs leading-relaxed text-muted">{arm.description}</p>}
      <Fields
        title="Règles"
        rows={[
          ['Rôle', arm.role === 'consensus' ? `consensus (≥ ${arm.min_confluence} bras d’accord)` : 'vote'],
          ['Activée', arm.enabled ? null : 'non'],
          ['Part du capital', `${arm.capital_pct} %`],
          ['Seuil alpha', arm.entry_threshold],
          ['Stop loss', arm.stop_loss_pct != null ? `${arm.stop_loss_pct} %` : null],
          ['Take profit 1', arm.take_profit_1 != null ? `+${arm.take_profit_1} %` : null],
          ['Durée maximale', arm.max_hold_time_minutes != null ? `${arm.max_hold_time_minutes} min` : null],
        ]}
      />
      <Fields
        title="Dernier cycle"
        rows={[
          ['Candidats vus', seen || null],
          ['Retenus', seen ? arm.kept : null],
          ['Au-dessus du seuil', seen ? arm.qualified : null],
        ]}
      />
      {s && trades > 0 && (
        <Fields
          title="Résultats"
          rows={[
            ['P&L cumulé', `${s.total_pnl_usd >= 0 ? '+' : ''}${usd(s.total_pnl_usd, 2)}`, pnlColor(s.total_pnl_usd)],
            ['Trades', trades],
            ['Positions ouvertes', s.open_positions || null],
            ['Win rate', pct(s.win_rate)],
            ['Profit factor', factor(s.profit_factor)],
            ['Gain moyen', pct(s.avg_win_pct)],
            ['Perte moyenne', pct(s.avg_loss_pct)],
            ['Drawdown max', `${s.max_drawdown_pct.toFixed(1)} %`],
            ['Équité', usd(s.equity, 2)],
          ]}
        />
      )}
    </>
  )
}
