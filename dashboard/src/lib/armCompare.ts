import { computeDrawdownSeries } from './drawdown'
import { summarizeArm } from './armSummary'
import { filterByRange, pnlBefore, type ResolvedRange } from './dateRange'
import type { ArmSummary } from '../components/backtester/ArmSummaryTable'

export const MIN_COMPARED = 2
export const MAX_COMPARED = 4

type TradeLike = { timestamp_exit: string; pnl_usd: number }
export type CompareInput = { arm: string; baseline: number; trades: readonly TradeLike[] }
export type ArmComparison = {
  arm: string
  /** P&L par trade dans la plage, chronologique. */
  series: number[]
  /** Cumul depuis 0 (point de départ inclus). */
  points: number[]
  drawdown: number[]
  summary: ArmSummary
}

/** Ajoute ou retire un bras ; au-delà de MAX_COMPARED l'ajout est ignoré. Pure. */
export function toggleSelection(selected: readonly string[], arm: string): string[] {
  if (selected.includes(arm)) return selected.filter((name) => name !== arm)
  return selected.length >= MAX_COMPARED ? [...selected] : [...selected, arm]
}

function cumulative(series: readonly number[]): number[] {
  let running = 0
  return [0, ...series.map((delta) => (running += delta))]
}

/**
 * Restreint chaque bras à la plage (date de clôture) et calcule courbes et
 * récap. L'équité de départ du drawdown = mise + P&L réalisé avant la plage,
 * pour ne pas mesurer un creux depuis un capital qui n'existait pas encore.
 */
export function buildComparison(inputs: readonly CompareInput[], range: ResolvedRange): ArmComparison[] {
  return inputs.map(({ arm, baseline, trades }) => {
    const ordered = [...trades].sort((a, b) => Date.parse(a.timestamp_exit) - Date.parse(b.timestamp_exit))
    const series = filterByRange(ordered, range).map((trade) => trade.pnl_usd)
    const startingEquity = baseline + pnlBefore(ordered, range.since)
    return {
      arm,
      series,
      points: cumulative(series),
      drawdown: computeDrawdownSeries(series, startingEquity),
      summary: summarizeArm(arm, series, startingEquity),
    }
  })
}
