import { useMemo, useState } from 'react'
import { useStore } from '../../lib/store'
import { EMPTY_ARRAY } from '../../lib/empty'
import { Panel, Empty } from '../Panel'
import { useArmEquity } from '../../lib/useArmEquity'
import { computeDrawdownSeries, computeMaxDrawdown } from '../../lib/drawdown'
import { armColor } from '../../lib/armColors'
import { ArmToggle } from './ArmToggle'
import { MultiCurveChart } from './MultiCurveChart'
import { DrawdownChart } from './DrawdownChart'
import { ArmSummaryTable } from './ArmSummaryTable'
import type { ArmSummary } from './ArmSummaryTable'

const DEFAULT_BASELINE = 1000

function cumulative(series: number[]): number[] {
  let running = 0
  return series.map((delta) => (running += delta))
}

/** P&L cumulé et drawdown par bras, comparaison multi-courbes. Fetch parallèle au montage. */
export function Backtester() {
  const arms = useStore((s) => s.state.arms ?? EMPTY_ARRAY)
  const connected = useStore((s) => s.connected)
  const aggregateEquity = useStore((s) => s.equitySeries)
  const results = useArmEquity(arms)
  const [visible, setVisible] = useState<Set<string> | null>(null)
  const [highlighted, setHighlighted] = useState<string | null>(null)

  const armByName = useMemo(() => new Map(arms.map((arm) => [arm.name, arm])), [arms])
  const armNames = results.map((r) => r.arm)
  const effectiveVisible = visible ?? new Set(armNames)

  const curves = useMemo(
    () =>
      results.map((result, index) => ({
        arm: result.arm,
        points: [0, ...cumulative(result.series)],
        color: armColor(index),
      })),
    [results],
  )

  const drawdownCurves = useMemo(
    () =>
      results.map((result, index) => {
        const baseline = armByName.get(result.arm)?.stats?.baseline ?? DEFAULT_BASELINE
        return { arm: result.arm, points: computeDrawdownSeries(result.series, baseline), color: armColor(index) }
      }),
    [results, armByName],
  )

  const summaries: ArmSummary[] = useMemo(
    () =>
      results.map((result) => {
        const baseline = armByName.get(result.arm)?.stats?.baseline ?? DEFAULT_BASELINE
        const wins = result.series.filter((v) => v > 0)
        const losses = result.series.filter((v) => v < 0)
        const grossWin = wins.reduce((sum, v) => sum + v, 0)
        const grossLoss = Math.abs(losses.reduce((sum, v) => sum + v, 0))
        return {
          arm: result.arm,
          trades: result.series.length,
          winRate: result.series.length ? (100 * wins.length) / result.series.length : 0,
          profitFactor: grossLoss > 0 ? grossWin / grossLoss : grossWin > 0 ? Infinity : 0,
          pnlTotal: result.series.reduce((sum, v) => sum + v, 0),
          maxDrawdownPct: computeMaxDrawdown(result.series, baseline),
        }
      }),
    [results, armByName],
  )

  const hasHistory = results.some((result) => result.series.length > 0)
  const loading = results.length > 0 && results.every((result) => result.status === 'loading')

  const toggleArm = (arm: string) => {
    setVisible((prev) => {
      const base = prev ?? new Set(armNames)
      const next = new Set(base)
      if (next.has(arm)) next.delete(arm)
      else next.add(arm)
      return next
    })
  }

  if (arms.length === 0) {
    return (
      <Panel title="Backtester">
        <Empty>Aucun historique de trades disponible.</Empty>
      </Panel>
    )
  }

  return (
    <div className="space-y-4">
      {!connected && (
        <p className="text-[11px] text-warn">
          Connexion perdue — les données affichées sont le dernier historique chargé.
        </p>
      )}

      <Panel title="P&L cumulé par stratégie">
        {loading ? (
          <p className="px-1 py-6 text-center text-[11px] text-dim">Chargement de l'historique…</p>
        ) : !hasHistory ? (
          <Empty>Aucun historique de trades disponible.</Empty>
        ) : (
          <>
            <div className="mb-3">
              <ArmToggle
                arms={armNames}
                colors={Object.fromEntries(results.map((result, index) => [result.arm, armColor(index).stroke]))}
                visible={effectiveVisible}
                onToggle={toggleArm}
              />
            </div>
            <MultiCurveChart
              curves={curves}
              visible={effectiveVisible}
              highlighted={highlighted}
              aggregate={[0, ...cumulative(aggregateEquity)]}
            />
          </>
        )}
      </Panel>

      {hasHistory && !loading && (
        <>
          <Panel title="Drawdown par stratégie">
            <DrawdownChart curves={drawdownCurves} visible={effectiveVisible} highlighted={highlighted} />
          </Panel>

          <Panel title="Récapitulatif">
            <ArmSummaryTable summaries={summaries} highlighted={highlighted} onSelect={setHighlighted} />
          </Panel>
        </>
      )}
    </div>
  )
}
