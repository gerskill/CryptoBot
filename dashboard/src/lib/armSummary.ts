import { computeMaxDrawdown } from './drawdown'
import type { ArmSummary } from '../components/backtester/ArmSummaryTable'

/** Récap d'un bras depuis ses P&L par trade (chronologiques). */
export function summarizeArm(arm: string, series: readonly number[], startingEquity: number): ArmSummary {
  const wins = series.filter((v) => v > 0)
  const grossWin = wins.reduce((sum, v) => sum + v, 0)
  const grossLoss = Math.abs(series.filter((v) => v < 0).reduce((sum, v) => sum + v, 0))
  return {
    arm,
    trades: series.length,
    winRate: series.length ? (100 * wins.length) / series.length : 0,
    profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0,
    pnlTotal: series.reduce((sum, v) => sum + v, 0),
    maxDrawdownPct: computeMaxDrawdown([...series], startingEquity),
  }
}
