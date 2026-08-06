type Props = {
  arms: string[]
  colors: Record<string, string>
  visible: Set<string>
  onToggle: (arm: string) => void
}

/** Cases à cocher stylées en badge, couleur reprise de la courbe correspondante. */
export function ArmToggle({ arms, colors, visible, onToggle }: Props) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {arms.map((arm) => {
        const checked = visible.has(arm)
        const color = colors[arm] ?? 'var(--color-dim)'
        return (
          <button
            key={arm}
            onClick={() => onToggle(arm)}
            aria-pressed={checked}
            className="rounded border px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide transition-colors"
            style={{
              color: checked ? color : 'var(--color-dim)',
              borderColor: checked ? color : 'var(--color-edge)',
              backgroundColor: checked ? `color-mix(in srgb, ${color} 14%, transparent)` : 'transparent',
            }}
          >
            {arm}
          </button>
        )
      })}
    </div>
  )
}
