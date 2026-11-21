import type { RangeChoice, RangePreset } from '../../lib/dateRange'

const PRESETS: { id: RangePreset; label: string }[] = [
  { id: '7d', label: '7 jours' },
  { id: '30d', label: '30 jours' },
  { id: 'all', label: 'Tout' },
  { id: 'custom', label: 'Dates' },
]

const DATE_INPUT = 'mt-1 block min-h-11 rounded-md bg-void px-3 font-mono text-[12px] text-ink ring-1 ring-edge-strong'

/** Plage de la comparaison : préréglages ou deux dates (début et fin inclus). */
export function RangePicker({ value, onChange }: { value: RangeChoice; onChange: (next: RangeChoice) => void }) {
  return (
    <div>
      <div role="group" aria-label="Plage de dates" className="flex flex-wrap gap-1.5">
        {PRESETS.map((preset) => {
          const active = value.preset === preset.id
          return (
            <button
              key={preset.id}
              type="button"
              aria-pressed={active}
              onClick={() => onChange({ ...value, preset: preset.id })}
              className={`min-h-11 rounded border px-3 font-mono text-[11px] uppercase tracking-wide transition-colors active:scale-[0.97] ${
                active ? 'border-gem bg-gem/15 text-ink' : 'border-edge text-dim hover:text-ink'
              }`}
            >
              {active ? '✓ ' : ''}
              {preset.label}
            </button>
          )
        })}
      </div>
      {value.preset === 'custom' && (
        <div className="mt-2 flex flex-wrap gap-3">
          <label className="text-[11px] text-muted">
            Du
            <input
              type="date"
              value={value.from ?? ''}
              onChange={(event) => onChange({ ...value, from: event.target.value })}
              className={DATE_INPUT}
            />
          </label>
          <label className="text-[11px] text-muted">
            Au (inclus)
            <input
              type="date"
              value={value.to ?? ''}
              onChange={(event) => onChange({ ...value, to: event.target.value })}
              className={DATE_INPUT}
            />
          </label>
        </div>
      )}
    </div>
  )
}
