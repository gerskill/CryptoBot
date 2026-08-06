import type { Arm } from '../../lib/types'

type Props = {
  arms: Arm[]
  selected: string | null
  onSelect: (name: string) => void
}

/** Tabs horizontales, même grammaire que la NavBar : underline gem sur l'actif. */
export function ArmPicker({ arms, selected, onSelect }: Props) {
  return (
    <div className="flex flex-wrap border-b border-edge/60">
      {arms.map((arm) => {
        const active = arm.name === selected
        return (
          <button
            key={arm.name}
            onClick={() => onSelect(arm.name)}
            className={`relative px-3 py-2 font-mono text-[10px] uppercase tracking-[0.15em] transition-colors ${
              active ? 'text-ink' : 'text-dim hover:text-ink'
            }`}
          >
            {arm.name}
            {active && <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-gem" />}
          </button>
        )
      })}
    </div>
  )
}
