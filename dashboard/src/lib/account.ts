import type { CompareInput } from './armCompare'
import type { ArmSummary } from '../components/backtester/ArmSummaryTable'

export const FLEET_ARM = 'compte'
export const DEFAULT_PICKED = 4

/**
 * Le compte = la somme des bras : tous les trades fusionnés, mise = somme des
 * mises. Une mise inconnue sur UN bras rend celle du compte inconnue : le
 * drawdown n'est alors pas calculé plutôt qu'inventé.
 */
export function fleetInput(inputs: readonly CompareInput[]): CompareInput {
  const known = inputs.length > 0 && inputs.every((input) => input.baseline !== null && input.baseline > 0)
  return {
    arm: FLEET_ARM,
    baseline: known ? inputs.reduce((sum, input) => sum + (input.baseline ?? 0), 0) : null,
    trades: inputs.flatMap((input) => input.trades),
  }
}

/** Bras tracés par défaut : ceux qui ont tradé, plus gros |P&L| d'abord. */
export function defaultPicked(summaries: readonly ArmSummary[], max = DEFAULT_PICKED): string[] {
  return summaries
    .filter((summary) => summary.trades > 0)
    .sort((a, b) => Math.abs(b.pnlTotal) - Math.abs(a.pnlTotal))
    .slice(0, max)
    .map((summary) => summary.arm)
}
