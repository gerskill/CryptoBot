import { formatWeekLabel } from '../../lib/weeklyStats'

type Props = {
  weekOffset: number
  onChange: (offset: number) => void
}

export function WeekSelector({ weekOffset, onChange }: Props) {
  return (
    <div className="flex items-center gap-3">
      <button
        onClick={() => onChange(weekOffset - 1)}
        aria-label="Semaine précédente"
        className="flex h-8 w-8 items-center justify-center rounded font-mono text-[11px] text-dim ring-1 ring-edge/60 transition-[color,transform] duration-150 hover:text-ink hover:ring-edge-strong active:scale-[0.94]"
      >
        ←
      </button>
      <span className="font-mono text-[11px] text-muted">{formatWeekLabel(weekOffset)}</span>
      <button
        onClick={() => onChange(weekOffset + 1)}
        disabled={weekOffset === 0}
        aria-label="Semaine suivante"
        className="flex h-8 w-8 items-center justify-center rounded font-mono text-[11px] text-dim ring-1 ring-edge/60 transition-[color,transform] duration-150 hover:text-ink hover:ring-edge-strong active:scale-[0.94] disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:text-dim disabled:hover:ring-edge/60 disabled:active:scale-100"
      >
        →
      </button>
    </div>
  )
}
