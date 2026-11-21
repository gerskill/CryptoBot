import type { SortState } from '../../lib/explore'

type AriaSort = 'ascending' | 'descending' | 'none'

const ARROW: Record<AriaSort, string> = { ascending: '↑', descending: '↓', none: '' }

/**
 * En-tête de colonne triable : le bouton est DANS le `<th>`, qui porte
 * `aria-sort`. Un `<th onClick>` seul n'est ni focalisable ni annoncé.
 */
export function SortableTh({
  label, sortKey, ariaSort, onSort, align = 'left', className = '',
}: {
  label: string
  sortKey: string
  ariaSort: AriaSort
  onSort: (key: string) => void
  align?: 'left' | 'right'
  className?: string
}) {
  return (
    <th aria-sort={ariaSort} className={`px-0 py-0 font-medium ${className}`}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`flex min-h-11 w-full items-center gap-1 px-2 uppercase tracking-wider transition-colors hover:text-ink ${
          align === 'right' ? 'justify-end' : 'justify-start'
        } ${ariaSort === 'none' ? '' : 'text-ink'}`}
      >
        {label}
        <span aria-hidden="true" className="inline-block w-3 text-left">{ARROW[ariaSort]}</span>
      </button>
    </th>
  )
}

/**
 * Tri pour les listes en cartes, qui n'ont pas d'en-têtes de colonnes.
 * `aria-sort` n'existe que sur un en-tête : ici, des boutons à bascule.
 */
export function SortBar({
  options, sort, onSort,
}: {
  options: ReadonlyArray<{ key: string; label: string }>
  sort: SortState | null
  onSort: (key: string) => void
}) {
  return (
    <div role="group" aria-label="Trier par" className="flex flex-wrap items-center gap-1.5">
      <span className="text-[11px] uppercase tracking-wider text-dim">Trier</span>
      {options.map(({ key, label }) => {
        const active = sort?.key === key
        return (
          <button
            key={key}
            type="button"
            aria-pressed={active}
            onClick={() => onSort(key)}
            className={`min-h-11 rounded-md px-3 text-[11px] ring-1 transition-colors ${
              active ? 'text-ink ring-edge-strong bg-raised' : 'text-muted ring-edge hover:text-ink'
            }`}
          >
            {label}
            {active && <span aria-hidden="true" className="ml-1">{sort.dir === 'asc' ? '↑' : '↓'}</span>}
          </button>
        )
      })}
    </div>
  )
}
