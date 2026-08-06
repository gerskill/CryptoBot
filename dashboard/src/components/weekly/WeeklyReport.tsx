import { useMemo, useState } from 'react'
import { useStore } from '../../lib/store'
import { EMPTY_ARRAY } from '../../lib/empty'
import { Panel, Empty } from '../Panel'
import { usd, pct } from '../../lib/format'
import { filterTradesByWeek, computeArmStats, buildWeeklyEquitySeries } from '../../lib/weeklyStats'
import type { ArmWeeklyStat } from '../../lib/weeklyStats'
import { WeekSelector } from './WeekSelector'
import { WeeklyEquityCurve } from './WeeklyEquityCurve'
import { ArmWeeklyTable } from './ArmWeeklyTable'
import { BestWorstTrades } from './BestWorstTrades'

const ZERO_STAT = (arm: string): ArmWeeklyStat => ({
  arm,
  trades: 0,
  wins: 0,
  winRate: 0,
  profitFactor: 0,
  pnlUsd: 0,
})

/** Rapport hebdomadaire calculé côté client : zéro endpoint supplémentaire. */
export function WeeklyReport() {
  const allTrades = useStore((s) => s.trades ?? EMPTY_ARRAY)
  const arms = useStore((s) => s.state.arms ?? EMPTY_ARRAY)
  const startingEquity = useStore((s) => {
    const agg = s.state.aggregate
    if (agg) return agg.equity - agg.total_pnl_usd
    const stats = s.state.stats
    return stats ? stats.equity - stats.total_pnl_usd : 1000
  })
  const [weekOffset, setWeekOffset] = useState(0)

  const weekTrades = useMemo(() => filterTradesByWeek(allTrades, weekOffset), [allTrades, weekOffset])

  // Le tableau par bras doit rester correct même pour un bras sans trade
  // cette semaine : on complète les stats calculées avec une ligne zéro pour
  // chaque bras connu qui n'apparaît pas dans `weekTrades`.
  const armStats = useMemo(() => {
    const computed = computeArmStats(weekTrades)
    const byName = new Map(computed.map((stat) => [stat.arm, stat]))
    const names = new Set<string>([...byName.keys(), ...arms.map((arm) => arm.name)])
    return Array.from(names).map((name) => byName.get(name) ?? ZERO_STAT(name))
  }, [weekTrades, arms])

  const equitySeries = useMemo(
    () => [startingEquity, ...buildWeeklyEquitySeries(weekTrades, startingEquity)],
    [weekTrades, startingEquity],
  )

  const pnlUsd = weekTrades.reduce((sum, trade) => sum + trade.pnl_usd, 0)
  const pnlPct = startingEquity > 0 ? (100 * pnlUsd) / startingEquity : 0

  return (
    <div className="space-y-4">
      <WeekSelector weekOffset={weekOffset} onChange={setWeekOffset} />

      {weekTrades.length === 0 ? (
        <Panel title="Rapport hebdomadaire">
          <Empty>Aucun trade clôturé cette semaine.</Empty>
        </Panel>
      ) : (
        <>
          <Panel
            title="P&L de la semaine"
            figure={`${pnlUsd >= 0 ? '+' : ''}${usd(pnlUsd, 2)} (${pct(pnlPct)})`}
            figureTone={pnlUsd >= 0 ? 'gain' : 'loss'}
          >
            <WeeklyEquityCurve key={weekOffset} series={equitySeries} />
          </Panel>

          <div className="grid gap-4 xl:grid-cols-2">
            <Panel title="Par stratégie">
              <ArmWeeklyTable stats={armStats} />
            </Panel>
            <Panel title="Meilleurs / pires trades">
              <BestWorstTrades trades={weekTrades} />
            </Panel>
          </div>
        </>
      )}
    </div>
  )
}
