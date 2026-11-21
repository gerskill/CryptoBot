import { useMemo, useState } from 'react'
import { useStore } from '../../lib/store'
import { EMPTY_ARRAY } from '../../lib/empty'
import { Panel, Empty } from '../Panel'
import { armColor } from '../../lib/armColors'
import { buildComparison, MAX_COMPARED, MIN_COMPARED, toggleSelection } from '../../lib/armCompare'
import { isInvalidRange, resolveRange, type RangeChoice } from '../../lib/dateRange'
import { useArmTrades } from '../../lib/useArmTrades'
import { ArmToggle } from '../backtester/ArmToggle'
import { MultiCurveChart } from '../backtester/MultiCurveChart'
import { DrawdownChart } from '../backtester/DrawdownChart'
import { ArmSummaryTable } from '../backtester/ArmSummaryTable'
import { RangePicker } from './RangePicker'

const DEFAULT_RANGE: RangeChoice = { preset: '7d' }

/**
 * Compare 2 à 4 bras sur une plage de dates. Données : tous les trades de
 * chaque bras (`/api/trades/range`), filtrés ici sur la date de clôture.
 */
export function ArmCompare() {
  const arms = useStore((s) => s.state.arms ?? EMPTY_ARRAY)
  const [picked, setPicked] = useState<string[] | null>(null)
  const [choice, setChoice] = useState<RangeChoice>(DEFAULT_RANGE)
  const [highlighted, setHighlighted] = useState<string | null>(null)

  const names = arms.map((arm) => arm.name)
  const selected = (picked ?? names.slice(0, MIN_COMPARED)).filter((name) => names.includes(name))
  const fetched = useArmTrades(selected)
  // « 7 j » se recalcule chaque minute : sinon la borne reste figée à l'ouverture.
  const minute = Math.floor(Date.now() / 60_000)
  const range = useMemo(() => resolveRange(choice), [choice, minute])
  const openEnded = choice.preset === 'custom' && !choice.from && !choice.to
  const invalid = isInvalidRange(range)

  const comparison = useMemo(
    () =>
      buildComparison(
        fetched.map((result) => ({
          arm: result.arm,
          trades: result.trades,
          baseline: arms.find((arm) => arm.name === result.arm)?.stats?.baseline ?? null,
        })),
        range,
      ),
    [fetched, arms, range],
  )

  if (arms.length < MIN_COMPARED) {
    return (
      <Panel title="Comparer des bras">
        <Empty>Il faut au moins deux bras publiés pour comparer.</Empty>
      </Panel>
    )
  }

  const colors = Object.fromEntries(selected.map((name, index) => [name, armColor(index).stroke]))
  const visible = new Set(selected)
  const loading = fetched.some((result) => result.status === 'loading')
  const failed = fetched.filter((result) => result.status === 'error').map((result) => result.arm)
  const hasTrades = comparison.some((item) => item.series.length > 0)
  const tooFew = selected.length < MIN_COMPARED

  return (
    <Panel
      title="Comparer des bras"
      hint={`Choisis ${MIN_COMPARED} à ${MAX_COMPARED} bras et une plage. La plage porte sur la date de clôture des trades ; le drawdown part de la mise du bras plus son P&L réalisé avant la plage.`}
    >
      <div className="space-y-3">
        <ArmToggle
          arms={names}
          colors={colors}
          visible={visible}
          onToggle={(arm) => setPicked(toggleSelection(selected, arm))}
        />
        <p className="text-[11px] text-dim" role="status">
          {selected.length} bras sélectionné{selected.length > 1 ? 's' : ''} sur {MAX_COMPARED} maximum.
        </p>
        <RangePicker value={choice} onChange={setChoice} />
      </div>

      <div className="mt-4">
        {tooFew ? (
          <Empty>Sélectionne au moins {MIN_COMPARED} bras.</Empty>
        ) : invalid ? (
          <p className="text-[12px] text-warn" role="alert">
            La date de début est après la date de fin : aucune plage à comparer.
          </p>
        ) : loading ? (
          <p className="px-1 py-6 text-center text-[11px] text-dim">Chargement des trades…</p>
        ) : (
          <div className="space-y-3">
            {failed.length > 0 && (
              <p className="text-[12px] text-warn" role="alert">
                Trades indisponibles pour : {failed.join(', ')}. Ces bras apparaissent sans données.
              </p>
            )}
            {openEnded && (
              <p className="text-[11px] text-dim">Aucune date saisie : toute la période est comparée.</p>
            )}
            {comparison.some((item) => !item.baselineKnown) && (
              <p className="text-[11px] text-warn" role="alert">
                Baseline inconnue pour {comparison.filter((item) => !item.baselineKnown).map((item) => item.arm).join(', ')} :
                drawdown non calculé.
              </p>
            )}
            {!hasTrades && <Empty>Aucun trade clôturé sur cette plage pour ces bras.</Empty>}
            {hasTrades && (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                <div className="min-w-0 space-y-4">
                  <div>
                    <h3 className="mb-1.5 text-[11px] uppercase tracking-wide text-dim">P&amp;L cumulé sur la plage</h3>
                    <MultiCurveChart
                      curves={comparison.map((item, index) => ({ arm: item.arm, points: item.points, color: armColor(index) }))}
                      visible={visible}
                      highlighted={highlighted}
                    />
                  </div>
                  <div>
                    <h3 className="mb-1.5 text-[11px] uppercase tracking-wide text-dim">Drawdown sur la plage</h3>
                    <DrawdownChart
                      curves={comparison
                        .map((item, index) => ({ arm: item.arm, points: item.drawdown, color: armColor(index) }))
                        .filter((curve) => curve.points.length > 0)}
                      visible={visible}
                      highlighted={highlighted}
                    />
                  </div>
                </div>
                <div className="min-w-0 overflow-x-auto">
                  <ArmSummaryTable
                    summaries={comparison.map((item) => item.summary)}
                    highlighted={highlighted}
                    onSelect={setHighlighted}
                  />
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </Panel>
  )
}
