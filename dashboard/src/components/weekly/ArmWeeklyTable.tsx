import type { ArmWeeklyStat } from '../../lib/weeklyStats'
import { usd, pnlColor } from '../../lib/format'

type Props = { stats: ArmWeeklyStat[] }

function formatPf(pf: number): string {
  if (!Number.isFinite(pf)) return '∞'
  if (pf === 0) return '—'
  return pf.toFixed(2)
}

/** Colonnes STRATÉGIE | TRADES | WR | PF | P&L, triées par P&L décroissant. */
export function ArmWeeklyTable({ stats }: Props) {
  const sorted = [...stats].sort((a, b) => b.pnlUsd - a.pnlUsd)

  return (
    <table className="w-full border-collapse font-mono text-[11px]">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-wide text-dim">
          <th className="pb-1.5 font-medium">Stratégie</th>
          <th className="pb-1.5 text-right font-medium">Trades</th>
          <th className="pb-1.5 text-right font-medium">WR</th>
          <th className="pb-1.5 text-right font-medium">PF</th>
          <th className="pb-1.5 text-right font-medium">P&amp;L</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => (
          <tr key={row.arm} className="border-t border-edge/40">
            <td className="py-1.5 text-ink">{row.arm}</td>
            <td className="py-1.5 text-right tabular-nums text-dim">{row.trades || '—'}</td>
            <td className="py-1.5 text-right tabular-nums text-dim">
              {row.trades ? `${row.winRate.toFixed(0)}%` : '—'}
            </td>
            <td className="py-1.5 text-right tabular-nums text-dim">{formatPf(row.profitFactor)}</td>
            <td className={`py-1.5 text-right tabular-nums ${row.trades ? pnlColor(row.pnlUsd) : 'text-dim'}`}>
              {row.trades ? usd(row.pnlUsd, 2) : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
