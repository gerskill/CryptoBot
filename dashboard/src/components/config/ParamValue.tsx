import { useState } from 'react'

type Props = {
  value: unknown
  depth?: number
}

const MAX_INLINE_DEPTH = 2

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * Rendu récursif d'une valeur de paramètre : nombre, booléen, null, objet ou
 * tableau imbriqué. Deux niveaux affichés d'emblée, au-delà « {...} » est
 * cliquable pour déplier localement — jamais de crash sur `null`/`[]`/`{}`.
 */
export function ParamValue({ value, depth = 0 }: Props) {
  const [expanded, setExpanded] = useState(false)

  if (value === null || value === undefined) {
    return <span className="text-dim">null</span>
  }
  if (typeof value === 'boolean') {
    return <span className={value ? 'text-toxic' : 'text-blood'}>{String(value)}</span>
  }
  if (typeof value === 'number') {
    return <span className="font-mono text-ink">{value}</span>
  }
  if (typeof value === 'string') {
    return <span className="text-ink">{value || '—'}</span>
  }

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="text-dim">[]</span>
    if (depth >= MAX_INLINE_DEPTH && !expanded) {
      return (
        <button
          onClick={() => setExpanded(true)}
          className="font-mono text-dim underline decoration-dotted hover:text-ink"
        >
          [{value.length}]
        </button>
      )
    }
    return (
      <ul className="space-y-0.5 pl-3">
        {value.map((item, index) => (
          <li key={index} className="font-mono text-[11px]">
            <ParamValue value={item} depth={depth + 1} />
          </li>
        ))}
      </ul>
    )
  }

  if (isPlainObject(value)) {
    const entries = Object.entries(value)
    if (entries.length === 0) return <span className="text-dim">{'{}'}</span>
    if (depth >= MAX_INLINE_DEPTH && !expanded) {
      return (
        <button
          onClick={() => setExpanded(true)}
          className="font-mono text-dim underline decoration-dotted hover:text-ink"
        >
          {'{...}'}
        </button>
      )
    }
    return (
      <dl className="space-y-0.5 pl-3">
        {entries.map(([key, entryValue]) => (
          <div key={key} className="flex items-baseline gap-2">
            <dt className="shrink-0 font-mono text-[10px] text-dim">{key}</dt>
            <dd className="min-w-0">
              <ParamValue value={entryValue} depth={depth + 1} />
            </dd>
          </div>
        ))}
      </dl>
    )
  }

  return <span className="text-dim">{String(value)}</span>
}
