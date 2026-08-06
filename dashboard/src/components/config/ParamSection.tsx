import { ParamValue } from './ParamValue'

const KNOWN_SECTIONS = ['scan', 'filters', 'exit_rules', 'risk_rules', 'learning']

type Entry = [string, unknown]

type Props = {
  params: Record<string, unknown>
}

function Section({ title, entries }: { title: string; entries: Entry[] }) {
  if (entries.length === 0) return null
  return (
    <div className="mb-4">
      <h3 className="mb-1.5 text-[10px] uppercase tracking-wide text-dim">{title}</h3>
      <dl className="space-y-1 rounded-lg px-3 py-2 ring-1 ring-edge/50">
        {entries.map(([key, value]) => (
          <div key={key} className="flex items-baseline gap-3 border-b border-edge/30 py-1 last:border-0">
            <dt className="w-40 shrink-0 truncate font-mono text-[10px] text-dim">{key}</dt>
            <dd className="min-w-0 flex-1 text-[11px]">
              <ParamValue value={value} />
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/** Regroupe les clés d'un JSON de paramètres par section logique connue. */
export function ParamSection({ params }: Props) {
  const known = KNOWN_SECTIONS.filter((name) => name in params)
  const rest = Object.keys(params).filter((name) => !KNOWN_SECTIONS.includes(name))

  return (
    <div>
      {known.map((name) => {
        const value = params[name]
        const entries: Entry[] =
          value && typeof value === 'object' && !Array.isArray(value)
            ? Object.entries(value as Record<string, unknown>)
            : [[name, value]]
        return <Section key={name} title={name} entries={entries} />
      })}
      {rest.length > 0 && (
        <Section title="autres" entries={rest.map((key): Entry => [key, params[key]])} />
      )}
    </div>
  )
}
