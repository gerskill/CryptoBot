import type { ReactNode } from 'react'
import { countLabel } from '../../lib/explore'

/**
 * Barre de recherche d'une liste : champ texte, compteur « N sur M »,
 * réinitialisation. Les filtres propres au panneau se glissent en `children`.
 * Le compteur est une région `aria-live` : un lecteur d'écran entend le
 * résultat d'un filtre sans avoir à chercher où il s'affiche.
 */
export function ExploreBar({
  label, placeholder, query, onQuery, shown, total, isFiltered, onReset, children,
}: {
  label: string
  placeholder?: string
  query: string
  onQuery: (value: string) => void
  shown: number
  total: number
  isFiltered: boolean
  onReset: () => void
  children?: ReactNode
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 px-1">
      <input
        type="search"
        aria-label={label}
        value={query}
        onChange={(event) => onQuery(event.target.value)}
        placeholder={placeholder ?? label}
        className="min-h-11 w-full min-w-0 rounded-md bg-void px-2.5 text-xs text-ink ring-1 ring-edge placeholder:text-dim sm:min-h-8 sm:w-48"
      />
      {children}
      <span aria-live="polite" className="font-mono text-[11px] tabular-nums text-muted">
        {countLabel(shown, total)}
      </span>
      {isFiltered && (
        <button
          type="button"
          onClick={onReset}
          className="min-h-11 rounded-md px-3 text-[11px] text-muted ring-1 ring-edge transition-colors hover:text-ink sm:min-h-8"
        >
          Réinitialiser
        </button>
      )}
    </div>
  )
}

/** Aucun résultat : on répète la requête, un blanc laisserait croire à une panne. */
export function NoResults({ query }: { query: string }) {
  const trimmed = query.trim()
  return (
    <p role="status" className="px-3 py-6 text-center text-xs leading-relaxed text-dim">
      {trimmed ? <>Aucun résultat pour « {trimmed} ».</> : <>Aucun résultat avec ces filtres.</>}
    </p>
  )
}
