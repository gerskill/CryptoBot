import { useMemo, useState } from 'react'
import { Panel, Empty } from '../Panel'
import { Drawer } from '../explore/Drawer'
import { BetDetail } from '../explore/BetDetail'
import { useDrawer, type OpenEvent } from '../explore/useDrawer'
import { useBetPrices } from '../../lib/usePrediction'
import type { PredictionBet, PredictionSummary } from '../../lib/predictionTypes'
import { ArmTable } from './ArmTable'
import { ArmStatsPanel } from './ArmStatsPanel'
import { BetChart } from './BetChart'
import { BetsPanel } from './BetsPanel'
import { useBetsExplore } from './betsExplore'

/**
 * Bras affiché : celui choisi, sinon le premier qui a des paris, sinon le
 * premier de la liste. Tous les chiffres de la page parlent de CE bras.
 */
function pickArm(arms: PredictionSummary['arms'], choice: string | null) {
  const fallback = arms.find((a) => a.stats.paris_regles + a.ouverts > 0)?.arm ?? arms[0]?.arm ?? null
  const name = arms.some((a) => a.arm === choice) ? choice : fallback
  return { name, arm: arms.find((a) => a.arm === name) ?? null }
}

export function LiquidBets({ summary, bets, demo }: {
  summary: PredictionSummary; bets: PredictionBet[]; demo: boolean
}) {
  const [armChoice, setArmChoice] = useState<string | null>(null)
  const [betChoice, setBetChoice] = useState<string | null>(null)
  const { name: armName, arm } = pickArm(summary.arms, armChoice)

  // Plus récents en premier : c'est ce qu'on vient regarder.
  const armBets = useMemo(() => bets.filter((b) => b.arm === armName).reverse(), [bets, armName])
  const model = useBetsExplore(armBets)
  const visible = model.explore.visible
  const bet = visible.find((b) => b.id === betChoice) ?? visible[0] ?? null

  const drawer = useDrawer<string>()
  const detail = bets.find((b) => b.id === drawer.item) ?? null
  // Une ligne sélectionne le pari (graphique de la page) ET ouvre son détail.
  const openBet = (id: string) => (event: OpenEvent) => {
    setBetChoice(id)
    drawer.openFrom(id)(event)
  }
  // Une seule requête de prix : celle du détail s'il est ouvert, sinon du pari suivi.
  const prices = useBetPrices(detail?.coin ?? bet?.coin ?? null, demo)

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <div className="grid min-w-0 grid-cols-1 gap-4">
        <Panel title="Bras de prédiction" count={summary.arms.length}
          hint="Clique un bras : les chiffres, les paris et le graphique de la page le suivent.">
          <ArmTable arms={summary.arms} selected={armName} onSelect={(name) => { setArmChoice(name); setBetChoice(null) }} />
        </Panel>
        <ArmStatsPanel arm={arm} armName={armName} cfg={summary.config} />
      </div>

      <div className="grid min-w-0 grid-cols-1 content-start gap-4">
        <Panel title={bet ? `Graphique du pari · ${bet.arm}` : 'Graphique du pari'}
          hint={bet ? `${bet.title} — côté ${bet.side_name} — pièce ${bet.coin}` : undefined}>
          {bet ? (
            <>
              <BetChart bet={bet} points={prices.coin === bet.coin ? prices.points : []} />
              {prices.error && <p className="mt-2 text-[11px] text-blood">Prix illisibles : {prices.error}</p>}
            </>
          ) : (
            <Empty>Clique un pari dans le tableau pour le voir sur son graphique.</Empty>
          )}
        </Panel>
        <BetsPanel model={model} armName={armName} selectedId={bet?.id ?? null} onOpen={openBet} />
      </div>

      <Drawer open={detail !== null} onClose={drawer.close}
        title={detail?.title ?? ''} subtitle={detail ? `bras ${detail.arm} — côté ${detail.side_name}` : undefined}>
        {detail && <BetDetail bet={detail} prices={prices} />}
      </Drawer>
    </div>
  )
}
