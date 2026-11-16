import { Empty } from '../Panel'
import { utc } from '../../lib/format'
import { useStore } from '../../lib/store'
import { usePrediction } from '../../lib/usePrediction'
import { LiquidBets } from './LiquidBets'
import { LiquidCoherence } from './LiquidCoherence'
import { LiquidMarkets } from './LiquidMarkets'
import { LiquidOverview } from './LiquidOverview'
import { LiquidPerps } from './LiquidPerps'

/** Bandeau permanent : ce qu'on regarde est-il réel, fictif, ou figé ? */
function Banner({ demo, online, updated }: { demo: boolean; online: boolean; updated: string | null }) {
  if (demo) {
    return (
      <div className="mb-4 rounded-xl border border-warn/50 bg-warn/10 px-4 py-3">
        <div className="text-sm font-semibold text-warn">Exemple fictif</div>
        <div className="mt-0.5 text-xs leading-snug text-warn/80">
          Tous les paris, prix et résultats affichés ici sont inventés par
          <code className="mx-1 font-mono">scripts/prediction_demo.py</code>
          pour essayer l'écran. Aucun ne vient d'un marché réel.
        </div>
      </div>
    )
  }
  if (online) return null
  return (
    <div className="mb-4 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3">
      <div className="text-sm font-medium text-warn">La simulation de prédiction ne tourne pas</div>
      <div className="mt-0.5 text-xs leading-snug text-warn/80">
        {updated ? `Dernier cycle : ${utc(updated)}.` : 'Aucun cycle enregistré.'} Les chiffres
        affichés sont le dernier état connu.
      </div>
      <code className="mt-2 block font-mono text-[11px] text-dim">python3 scripts/run_prediction.py</code>
    </div>
  )
}

export function LiquidDashboard() {
  const view = useStore((s) => s.liquidView)
  const demo = useStore((s) => s.liquidDemo)
  const setDemo = useStore((s) => s.setLiquidDemo)
  const { summary, bets, error } = usePrediction(demo)

  const toggle = (
    <button onClick={() => setDemo(!demo)}
      className="rounded-md px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-muted ring-1 ring-edge transition-colors hover:text-ink">
      {demo ? 'Revenir aux données réelles' : "Voir l'exemple fictif"}
    </button>
  )

  if (!summary) {
    return (
      <div>
        <div className="mb-4 flex justify-end">{toggle}</div>
        <Empty>{error ? `API injoignable (${error}). Lancer : uvicorn api.server:app --port 8000` : 'Chargement…'}</Empty>
      </div>
    )
  }

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-[11px] text-muted">
          Simulation papier, aucun pari réel. Capital fictif de {summary.config.capital_fictif_usd} $ par bras,
          mise de {(summary.config.mise_pct * 100).toFixed(0)} % par pari.
        </p>
        {toggle}
      </div>

      <Banner demo={summary.exemple_fictif} online={summary.runner_online} updated={summary.updated_at_iso} />

      {/* `key` : changer de mode repart d'un choix de bras et de pari vierge. */}
      {view === 'apercu' && <LiquidOverview summary={summary} />}
      {view === 'predictions' && <LiquidMarkets demo={demo} />}
      {view === 'marches' && <LiquidPerps />}
      {view === 'paris' && <LiquidBets key={String(demo)} summary={summary} bets={bets} demo={demo} />}
      {view === 'incoherences' && <LiquidCoherence demo={demo} />}
    </div>
  )
}
