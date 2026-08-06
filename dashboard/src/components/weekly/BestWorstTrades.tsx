import type { Trade } from '../../lib/types'
import { pct, duration, pnlColor } from '../../lib/format'

type Props = { trades: Trade[] }

function TradeCard({ trade, tone }: { trade: Trade; tone: 'gain' | 'loss' }) {
  const style = tone === 'gain' ? 'border-toxic/20 bg-toxic/[0.04]' : 'border-blood/20 bg-blood/[0.04]'
  return (
    <div className={`rounded-lg border px-3 py-2 ${style}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="truncate font-mono text-xs font-medium text-ink">{trade.token}</span>
        <span className={`font-mono text-xs tabular-nums ${pnlColor(trade.pnl_pct)}`}>{pct(trade.pnl_pct)}</span>
      </div>
      <div className="mt-1 flex items-center gap-2 font-mono text-[10px] text-dim">
        {trade.arm && <span className="shrink-0 rounded bg-white/10 px-1 py-px text-muted">{trade.arm}</span>}
        <span className="truncate">{trade.exit_reason}</span>
        <span className="ml-auto shrink-0">{duration(trade.duration_min)}</span>
      </div>
      {trade.peak_pct != null && (
        <div className="mt-1 font-mono text-[10px] text-dim">pic {pct(trade.peak_pct)}</div>
      )}
    </div>
  )
}

/** Top 3 gagnants / top 3 perdants de la semaine sélectionnée. */
export function BestWorstTrades({ trades }: Props) {
  const sorted = [...trades].sort((a, b) => b.pnl_pct - a.pnl_pct)
  const winners = sorted.slice(0, 3).filter((t) => t.pnl_pct > 0)
  const losers = sorted
    .slice(-3)
    .reverse()
    .filter((t) => t.pnl_pct < 0)

  if (winners.length === 0 && losers.length === 0) {
    return <p className="px-1 py-3 text-[11px] text-dim">Aucun trade avec P&amp;L net cette semaine.</p>
  }

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div>
        <h3 className="mb-1.5 text-[10px] uppercase tracking-wide text-dim">Meilleurs trades</h3>
        <div className="space-y-1.5">
          {winners.length === 0 ? (
            <p className="text-[11px] text-dim">Aucun gagnant cette semaine.</p>
          ) : (
            winners.map((trade) => <TradeCard key={trade.id} trade={trade} tone="gain" />)
          )}
        </div>
      </div>
      <div>
        <h3 className="mb-1.5 text-[10px] uppercase tracking-wide text-dim">Pires trades</h3>
        <div className="space-y-1.5">
          {losers.length === 0 ? (
            <p className="text-[11px] text-dim">Aucun perdant cette semaine.</p>
          ) : (
            losers.map((trade) => <TradeCard key={trade.id} trade={trade} tone="loss" />)
          )}
        </div>
      </div>
    </div>
  )
}
