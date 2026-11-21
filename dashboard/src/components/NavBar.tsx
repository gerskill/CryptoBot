import { useStore } from '../lib/store'
import type { LiquidView, Space, View } from '../lib/store'

const SPACES: { id: Space; label: string; sub: string }[] = [
  { id: 'memecoins', label: 'Memecoins', sub: 'Solana · bot Alpha Loop' },
  { id: 'liquid', label: 'Liquid', sub: 'marchés · prédictions' },
]

const MEME_TABS: { id: View; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'live', label: 'Live Prices' },
  { id: 'weekly', label: 'Weekly' },
  { id: 'config', label: 'Config' },
  { id: 'backtester', label: 'Backtester' },
  { id: 'pilotage', label: 'Pilotage' },
]

const LIQUID_TABS: { id: LiquidView; label: string }[] = [
  { id: 'apercu', label: 'Aperçu et bras' },
  { id: 'predictions', label: 'Prédictions' },
  { id: 'marches', label: 'Marchés' },
  { id: 'paris', label: 'Paris et graphique' },
  { id: 'incoherences', label: 'Incohérences' },
]

function Tabs<T extends string>({ tabs, current, onPick, accent }: {
  tabs: { id: T; label: string }[]; current: T; onPick: (id: T) => void; accent: string
}) {
  return (
    <>
      {tabs.map((tab) => {
        const active = tab.id === current
        return (
          <button
            key={tab.id}
            onClick={() => onPick(tab.id)}
            aria-current={active ? 'page' : undefined}
            className={[
              'relative shrink-0 px-4 py-3 font-mono text-[11px] uppercase tracking-[0.15em]',
              'transition-[color,transform] duration-150 active:scale-[0.97]',
              active ? 'text-ink' : 'text-dim hover:text-ink',
            ].join(' ')}
          >
            {tab.label}
            {active && <span className={`absolute bottom-0 left-0 right-0 h-0.5 ${accent}`} />}
          </button>
        )
      })}
    </>
  )
}

/** Choix de l'espace. Deux produits, deux jeux d'onglets, jamais mélangés. */
export function SpaceSwitch() {
  const space = useStore((s) => s.space)
  const setSpace = useStore((s) => s.setSpace)
  return (
    <div role="tablist" aria-label="Espace" className="flex gap-1 rounded-lg bg-surface p-1 ring-1 ring-edge/60">
      {SPACES.map((item) => {
        const active = item.id === space
        const dot = item.id === 'liquid' ? 'bg-tide' : 'bg-gem'
        return (
          <button
            key={item.id}
            role="tab"
            aria-selected={active}
            onClick={() => setSpace(item.id)}
            className={[
              'rounded-md px-3 py-1.5 text-left transition-colors',
              active ? 'bg-raised text-ink' : 'text-dim hover:text-ink',
            ].join(' ')}
          >
            <span className="flex items-center gap-1.5 text-[12px] font-semibold">
              <span className={`h-1.5 w-1.5 rounded-full ${active ? dot : 'bg-edge-strong'}`} />
              {item.label}
            </span>
            <span className="hidden text-[11px] text-dim sm:block">{item.sub}</span>
          </button>
        )
      })}
    </div>
  )
}

export function NavBar() {
  const space = useStore((s) => s.space)
  const view = useStore((s) => s.view)
  const setView = useStore((s) => s.setView)
  const liquidView = useStore((s) => s.liquidView)
  const setLiquidView = useStore((s) => s.setLiquidView)

  return (
    <nav aria-label="Navigation principale" className="border-b border-edge/60">
      <div className="mx-auto flex max-w-[1800px] gap-0 overflow-x-auto px-5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
        {space === 'memecoins'
          ? <Tabs tabs={MEME_TABS} current={view} onPick={setView} accent="bg-gem" />
          : <Tabs tabs={LIQUID_TABS} current={liquidView} onPick={setLiquidView} accent="bg-tide" />}
      </div>
    </nav>
  )
}
