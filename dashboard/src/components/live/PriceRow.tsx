import { usePulse } from '../../lib/useLiveState'
import { price, pct, compact, pnlColor, usd } from '../../lib/format'
import type { Position, Candidate } from '../../lib/types'

type PositionRowProps = {
  position: Position
}

export function PositionRow({ position }: PositionRowProps) {
  const pulse = usePulse(position.current_price)

  return (
    <li className="flex items-baseline justify-between gap-2 rounded-lg border border-edge/50 px-3 py-2">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-medium text-ink">{position.symbol}</span>
          {position.arm && (
            <span className="rounded bg-white/10 px-1.5 py-px font-mono text-[9px] text-muted">
              {position.arm}
            </span>
          )}
        </div>
        <div className="mt-0.5 font-mono text-[10px] text-dim">
          entrée {price(position.entry_price)}
        </div>
      </div>

      <div className="shrink-0 text-right">
        <div className={`font-mono text-xs tabular-nums ${pulse}`}>
          {position.current_price != null ? price(position.current_price) : '—'}
        </div>
        <div className={`font-mono text-[10px] tabular-nums ${pnlColor(position.pnl_pct)}`}>
          {pct(position.pnl_pct)} · {position.pnl_usd >= 0 ? '+' : ''}{usd(position.pnl_usd, 2)}
        </div>
      </div>
    </li>
  )
}

type CandidateRowProps = {
  candidate: Candidate
}

export function CandidateRow({ candidate }: CandidateRowProps) {
  const pulse = usePulse(candidate.price_usd)

  return (
    <li className="flex items-baseline justify-between gap-2 rounded-lg border border-edge/50 px-3 py-2">
      <div className="min-w-0 flex-1">
        <span className="font-mono text-xs font-medium text-ink">{candidate.symbol}</span>
        <div className="mt-0.5 flex items-center gap-2 font-mono text-[10px] text-dim">
          <span>α {candidate.alpha_score.toFixed(2)}</span>
          <span>liq ${compact(candidate.liquidity_usd)}</span>
        </div>
      </div>

      <div className="shrink-0 text-right">
        <div className={`font-mono text-xs tabular-nums ${pulse}`}>
          {price(candidate.price_usd)}
        </div>
        <div className="flex gap-2">
          <span className={`font-mono text-[10px] tabular-nums ${pnlColor(candidate.price_change_5m)}`}>
            {pct(candidate.price_change_5m)} 5m
          </span>
          <span className={`font-mono text-[10px] tabular-nums ${pnlColor(candidate.price_change_1h)}`}>
            {pct(candidate.price_change_1h)} 1h
          </span>
        </div>
      </div>
    </li>
  )
}
