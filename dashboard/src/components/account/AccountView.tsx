import { useMemo, useState } from 'react'
import { useStore } from '../../lib/store'
import { EMPTY_ARRAY } from '../../lib/empty'
import { Panel, Empty, Stat } from '../Panel'
import { armColor } from '../../lib/armColors'
import { buildComparison, MAX_COMPARED, toggleSelection } from '../../lib/armCompare'
import { defaultPicked, fleetInput, FLEET_ARM } from '../../lib/account'
import { isInvalidRange, resolveRange, type RangeChoice } from '../../lib/dateRange'
import { useArmTrades } from '../../lib/useArmTrades'
import { usd } from '../../lib/format'
import { ArmToggle } from '../backtester/ArmToggle'
import { MultiCurveChart } from '../backtester/MultiCurveChart'
import { DrawdownChart } from '../backtester/DrawdownChart'
import { ArmSummaryTable } from '../backtester/ArmSummaryTable'
import { RangePicker } from '../compare/RangePicker'

const DEFAULT_RANGE: RangeChoice = { preset: 'all' }
const FLEET_VISIBLE = new Set([FLEET_ARM])

const tone = (value: number) => (value > 0 ? 'gain' : value < 0 ? 'loss' : 'neutral')
const plain = (value: number) => `${value.toFixed(1)}%`
const factor = (value: number) => (!Number.isFinite(value) ? '∞' : value === 0 ? '—' : value.toFixed(2))

/**
 * Le compte d'un coup d'œil : courbe et drawdown du compte (somme des bras),
 * puis chaque bras. Même source que « Comparer des bras » (`/api/trades/range`,
 * déjà limité au compte paper courant) : aucun chiffre calculé à part.
 */
export function AccountView() {
  const arms = useStore((s) => s.state.arms ?? EMPTY_ARRAY)
  const aggregate = useStore((s) => s.state.aggregate)
  const [choice, setChoice] = useState<RangeChoice>(DEFAULT_RANGE)
  const [picked, setPicked] = useState<string[] | null>(null)
  const [highlighted, setHighlighted] = useState<string | null>(null)

  const names = arms.map((arm) => arm.name)
  const fetched = useArmTrades(names)
  const minute = Math.floor(Date.now() / 60_000)
  const range = useMemo(() => resolveRange(choice), [choice, minute])
  const invalid = isInvalidRange(range)
  const loading = names.length > 0 && (fetched.length === 0 || fetched.some((r) => r.status === 'loading'))
  const failed = fetched.filter((r) => r.status === 'error').map((r) => r.arm)

  const { fleet, perArm } = useMemo(() => {
    const inputs = fetched.map((result) => ({
      arm: result.arm,
      trades: result.trades,
      baseline: arms.find((arm) => arm.name === result.arm)?.stats?.baseline ?? null,
    }))
    const [fleetCurve] = buildComparison([fleetInput(inputs)], range)
    return { fleet: fleetCurve, perArm: buildComparison(inputs, range) }
  }, [fetched, arms, range])

  if (names.length === 0) {
    return (
      <Panel title="Compte">
        <Empty>Aucun bras publié : le bot n'a pas encore écrit son état.</Empty>
      </Panel>
    )
  }

  const selected = (picked ?? defaultPicked(perArm.map((item) => item.summary))).filter((n) => names.includes(n))
  const colors = Object.fromEntries(names.map((name, index) => [name, armColor(index).stroke]))
  const visible = new Set(selected)
  const summary = fleet?.summary

  return (
    <div className="space-y-4">
      <Panel title="Compte paper" hint="Somme de tous les bras, depuis l'ouverture du compte courant. Choisis la plage pour zoomer.">
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 px-1 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Équité" value={usd(aggregate?.equity ?? 0, 2)} />
            <Stat label="P&L réalisé" value={usd(summary?.pnlTotal ?? 0, 2)} tone={tone(summary?.pnlTotal ?? 0)} />
            <Stat label="Trades" value={String(summary?.trades ?? 0)} hint={`${aggregate?.open_positions ?? 0} ouverts`} />
            <Stat label="Win rate" value={summary?.trades ? plain(summary.winRate) : '—'} />
            <Stat label="Profit factor" value={summary ? factor(summary.profitFactor) : '—'} />
            <Stat
              label="Drawdown max"
              value={fleet?.baselineKnown ? plain(summary?.maxDrawdownPct ?? 0) : '—'}
              tone={fleet?.baselineKnown && (summary?.maxDrawdownPct ?? 0) > 0 ? 'warn' : 'neutral'}
              hint={`exposé ${usd(aggregate?.exposure_usd ?? 0, 0)}`}
            />
          </div>
          <RangePicker value={choice} onChange={setChoice} />
          {invalid ? (
            <p className="text-[12px] text-warn" role="alert">La date de début est après la date de fin.</p>
          ) : loading ? (
            <p className="px-1 py-6 text-center text-[11px] text-dim">Chargement des trades…</p>
          ) : !fleet || fleet.points.length < 2 ? (
            <Empty>Aucun trade clôturé sur cette plage : la courbe apparaît dès le premier.</Empty>
          ) : (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div className="min-w-0">
                <h3 className="mb-1.5 text-[11px] uppercase tracking-wide text-dim">
                  Courbe du compte (P&amp;L cumulé)
                </h3>
                <MultiCurveChart
                  curves={[{ arm: FLEET_ARM, points: fleet.points, color: armColor(0) }]}
                  visible={FLEET_VISIBLE}
                  highlighted={null}
                  digits={2}
                />
              </div>
              <div className="min-w-0">
                <h3 className="mb-1.5 text-[11px] uppercase tracking-wide text-dim">Drawdown du compte</h3>
                {fleet.baselineKnown ? (
                  <DrawdownChart
                    curves={[{ arm: FLEET_ARM, points: fleet.drawdown, color: armColor(1) }]}
                    visible={FLEET_VISIBLE}
                    highlighted={null}
                  />
                ) : (
                  <Empty>Mise d'un bras inconnue : drawdown non calculé plutôt qu'inventé.</Empty>
                )}
              </div>
            </div>
          )}
          {failed.length > 0 && (
            <p className="text-[12px] text-warn" role="alert">
              Trades indisponibles pour : {failed.join(', ')}. Ces bras manquent au compte.
            </p>
          )}
        </div>
      </Panel>

      <Panel title="Courbes par bras" count={`${selected.length}/${MAX_COMPARED}`} hint="Coche jusqu'à 4 bras. Par défaut : ceux qui ont tradé, plus gros écart d'abord.">
        <div className="space-y-4">
          <ArmToggle
            arms={names}
            colors={colors}
            visible={visible}
            onToggle={(arm) => setPicked(toggleSelection(selected, arm))}
          />
          {!loading && (
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <MultiCurveChart
                curves={perArm.map((item) => ({ arm: item.arm, points: item.points, color: armColor(names.indexOf(item.arm)) }))}
                visible={visible}
                highlighted={highlighted}
                digits={2}
              />
              <DrawdownChart
                curves={perArm
                  .map((item) => ({ arm: item.arm, points: item.drawdown, color: armColor(names.indexOf(item.arm)) }))
                  .filter((curve) => curve.points.length > 0)}
                visible={visible}
                highlighted={highlighted}
              />
            </div>
          )}
        </div>
      </Panel>

      <Panel title="Tous les bras" count={names.length} hint="Trié par P&L. Clique une ligne pour la mettre en évidence sur les courbes.">
        {loading ? (
          <p className="px-1 py-6 text-center text-[11px] text-dim">Chargement des trades…</p>
        ) : (
          <div className="min-w-0 overflow-x-auto">
            <ArmSummaryTable summaries={perArm.map((item) => item.summary)} highlighted={highlighted} onSelect={setHighlighted} />
          </div>
        )}
      </Panel>
    </div>
  )
}
