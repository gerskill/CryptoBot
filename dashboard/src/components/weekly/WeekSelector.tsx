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
        className="rounded px-2 py-1 font-mono text-[11px] text-dim ring-1 ring-edge/60 transition-colors hover:text-ink hover:ring-edge-strong"
      >
        ←
      </button>
      <span className="font-mono text-[11px] text-muted">{formatWeekLabel(weekOffset)}</span>
      <button
        onClick={() => onChange(weekOffset + 1)}
        disabled={weekOffset === 0}
        aria-label="Semaine suivante"
        className="rounded px-2 py-1 font-mono text-[11px] text-dim ring-1 ring-edge/60 transition-colors hover:text-ink hover:ring-edge-strong disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:text-dim disabled:hover:ring-edge/60"
      >
        →
      </button>
    </div>
  )
}
