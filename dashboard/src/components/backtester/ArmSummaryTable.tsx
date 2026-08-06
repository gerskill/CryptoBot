import { usd, pnlColor } from '../../lib/format'

export type ArmSummary = {
  arm: string
  trades: number
  winRate: number
  profitFactor: number
  pnlTotal: number
  maxDrawdownPct: number
}

type Props = {
  summaries: ArmSummary[]
  highlighted: string | null
  onSelect: (arm: string | null) => void
}

function formatPf(pf: number): string {
  if (!Number.isFinite(pf)) return '∞'
  if (pf === 0) return '—'
  return pf.toFixed(2)
}

/** Récap par bras, trié P&L décroissant. La meilleure ligne porte un liseré toxic. */
export function ArmSummaryTable({ summaries, highlighted, onSelect }: Props) {
  const sorted = [...summaries].sort((a, b) => b.pnlTotal - a.pnlTotal)
  const bestArm = sorted[0]?.arm

  return (
    <table className="w-full border-collapse font-mono text-[11px]">
      <thead>
        <tr className="text-left text-[10px] uppercase tracking-wide text-dim">
          <th className="pb-1.5 font-medium">Bras</th>
          <th className="pb-1.5 text-right font-medium">Trades</th>
          <th className="pb-1.5 text-right font-medium">WR</th>
          <th className="pb-1.5 text-right font-medium">PF</th>
          <th className="pb-1.5 text-right font-medium">P&amp;L total</th>
          <th className="pb-1.5 text-right font-medium">Drawdown max</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((row) => (
          <tr
            key={row.arm}
            onClick={() => onSelect(highlighted === row.arm ? null : row.arm)}
            className={`cursor-pointer border-t border-edge/40 transition-colors hover:bg-white/[0.03] ${
              row.arm === bestArm ? 'border-l-2 border-l-toxic' : ''
            } ${highlighted === row.arm ? 'bg-gem/[0.05]' : ''}`}
          >
            <td className="py-1.5 text-ink">{row.arm}</td>
            <td className="py-1.5 text-right tabular-nums text-dim">{row.trades || '—'}</td>
            <td className="py-1.5 text-right tabular-nums text-dim">
              {row.trades ? `${row.winRate.toFixed(0)}%` : '—'}
            </td>
            <td className="py-1.5 text-right tabular-nums text-dim">{formatPf(row.profitFactor)}</td>
            <td className={`py-1.5 text-right tabular-nums ${row.trades ? pnlColor(row.pnlTotal) : 'text-dim'}`}>
              {row.trades ? usd(row.pnlTotal, 2) : '—'}
            </td>
            <td className="py-1.5 text-right tabular-nums text-warn">
              {row.maxDrawdownPct > 0 ? `-${row.maxDrawdownPct.toFixed(1)}%` : '—'}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
