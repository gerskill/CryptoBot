import { useStore } from '../lib/store'
import type { View } from '../lib/store'

const TABS: { id: View; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'live', label: 'Live Prices' },
  { id: 'weekly', label: 'Weekly' },
  { id: 'config', label: 'Config' },
  { id: 'backtester', label: 'Backtester' },
]

export function NavBar() {
  const view = useStore((s) => s.view)
  const setView = useStore((s) => s.setView)

  return (
    <nav
      aria-label="Navigation principale"
      className="border-b border-edge/60"
    >
      <div className="mx-auto flex max-w-[1800px] gap-0 px-5">
        {TABS.map((tab) => {
          const active = tab.id === view
          return (
            <button
              key={tab.id}
              onClick={() => setView(tab.id)}
              className={[
                'relative px-4 py-2.5 font-mono text-[10px] uppercase tracking-[0.15em] transition-colors',
                active
                  ? 'text-ink'
                  : 'text-dim hover:text-ink',
              ].join(' ')}
            >
              {tab.label}
              {active && (
                <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-gem" />
              )}
            </button>
          )
        })}
      </div>
    </nav>
  )
}
