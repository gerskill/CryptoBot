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
      {arm.role_label && <p className="mb-1 text-xs font-medium text-ink">{arm.role_label}</p>}
      {arm.description && <p className="text-xs leading-relaxed text-muted">{arm.description}</p>}
      <Fields
        title="Règles"
        rows={[
          [
            'Rôle',
            arm.role === 'consensus'
              ? `consensus (≥ ${arm.min_confluence} bras d’accord)`
              : arm.role === 'test'
                ? 'test (ne vote pas)'
                : 'vote',
          ],
          ['Activée', arm.enabled ? null : 'non'],
          ['État', arm.paused ? 'en pause (plus aucune entrée, positions jusqu’à leur sortie)' : null],
          ['Famille d’entrée', arm.famille ?? null],
          ['Apprentissage', arm.learning === false ? 'coupé (réglages figés)' : null],
          [
            'Réglages non lus',
            arm.reglages_non_lus && arm.reglages_non_lus.length
              ? `${arm.reglages_non_lus.length} (sans effet : ${arm.reglages_non_lus.join(', ')})`
              : null,
          ],
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
