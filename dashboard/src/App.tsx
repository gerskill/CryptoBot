import { useLiveState } from './lib/useLiveState'
import { useStore } from './lib/store'
import { Shell } from './components/Shell'
import { TheHunt } from './components/TheHunt'
import { ActivePositions } from './components/ActivePositions'
import { TheBrain } from './components/TheBrain'
import { TheArms } from './components/TheArms'

/** Bandeau d'alerte quand le bot ne tourne pas — un dashboard figé doit le dire. */
function OfflineBanner() {
  const online = useStore((s) => s.state.bot_online)
  const reason = useStore((s) => s.state.reason)
  if (online) return null

  return (
    <div className="mb-4 rounded-xl border border-warn/40 bg-warn/10 px-4 py-3">
      <div className="text-sm font-medium text-warn">Le bot ne tourne pas</div>
      <div className="mt-0.5 text-xs leading-snug text-warn/80">
        {reason ?? 'Aucun signe de vie récent.'} Les données affichées sont le dernier
        état connu, pas le marché actuel.
      </div>
      <code className="mt-2 block font-mono text-[11px] text-dim">python -m src.main</code>
    </div>
  )
}

export default function App() {
  useLiveState()

  return (
    <Shell>
      <OfflineBanner />
      {/* 4 colonnes sur très grand écran, 2 sur laptop, empilées en dessous.
          Les stratégies viennent en second : ce sont elles qui décident, et
          `state.candidates` ne montre que le bras témoin. */}
      <div className="grid gap-4 xl:h-[calc(100vh-8.5rem)] md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.15fr)_minmax(0,1fr)]">
        <TheHunt />
        <TheArms />
        <ActivePositions />
        <TheBrain />
      </div>
    </Shell>
  )
}
