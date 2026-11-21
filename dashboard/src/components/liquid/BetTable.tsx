import { useState } from 'react'
import { Empty } from '../Panel'
import { NoResults } from '../explore/ExploreBar'
import { SortableTh } from '../explore/SortControls'
import { rowClick, type OpenEvent } from '../explore/useDrawer'
import { pnlColor, utc, prob } from '../../lib/format'
import type { PredictionBet } from '../../lib/predictionTypes'
import { signed } from './liquidLabels'
import { issueLabel } from './betSort'

type Handler = (event: OpenEvent) => void

/** Au-delà, on pagine : des centaines de lignes à re-rendre à chaque message pèsent. */
const PAGE_SIZE = 100

const COLUMNS: { key: string; label: string; align?: 'right' }[] = [
  { key: 'entree', label: 'Entrée (UTC)' },
  { key: 'marche', label: 'Marché' },
  { key: 'cote', label: 'Côté' },
  { key: 'paye', label: 'Prix payé', align: 'right' },
  { key: 'estime', label: 'Estimé', align: 'right' },
  { key: 'issue', label: 'Issue', align: 'right' },
  { key: 'pnl', label: 'P&L net', align: 'right' },
]

/**
 * Tableau des paris d'un bras. Chaque ligne s'ouvre au clic ou au clavier
 * (Entrée / Espace) ; les en-têtes trient et portent `aria-sort`.
 */
export function BetTable({
  bets, total, query, label, selectedId, ariaSort, onSort, onOpen,
}: {
  bets: PredictionBet[]
  /** Paris du bras avant tout filtre : distingue « aucun pari » de « aucun résultat ». */
  total: number
  query: string
  label: string
  selectedId: string | null
  ariaSort: (key: string) => 'ascending' | 'descending' | 'none'
  onSort: (key: string) => void
  onOpen: (id: string) => Handler
}) {
  const [limit, setLimit] = useState(PAGE_SIZE)
  if (total === 0) return <Empty>Aucun pari pour ce bras.</Empty>
  if (bets.length === 0) return <NoResults query={query} />
  const shown = bets.slice(0, limit)
  return (
    <div className="max-h-[420px] overflow-auto">
      <table aria-label={label} className="w-full min-w-[640px] whitespace-nowrap text-left text-[11px]">
        <thead className="sticky top-0 bg-surface text-[11px] uppercase tracking-wider text-dim">
          <tr>
            {COLUMNS.map((c) => (
              <SortableTh key={c.key} label={c.label} sortKey={c.key} align={c.align}
                ariaSort={ariaSort(c.key)} onSort={onSort} />
            ))}
          </tr>
        </thead>
        <tbody className="font-mono tabular-nums">
          {shown.map((bet) => (
            <tr key={bet.id} {...rowClick(onOpen(bet.id))}
              className={`h-11 cursor-pointer border-t border-edge/40 transition-colors hover:bg-raised ${bet.id === selectedId ? 'bg-raised' : ''}`}>
              <td className="whitespace-nowrap px-2 py-2 text-muted">{utc(bet.entry_time)}</td>
              <td className="whitespace-normal px-2 py-0 font-sans text-ink">
                {/* Le vrai bouton de la ligne : focalisable, annoncé comme ouvrant un détail. */}
                <button type="button" data-row-trigger aria-haspopup="dialog"
                  className="min-h-11 w-full text-left">{bet.title}</button>
              </td>
              <td className="px-2 py-2 text-muted">{bet.side_name}</td>
              <td className="px-2 py-2 text-right text-ink">{prob(bet.entry_price)}</td>
              <td className="px-2 py-2 text-right text-muted">{prob(bet.prob_estimee)}</td>
              <td className="px-2 py-2 text-right text-muted">{issueLabel(bet)}</td>
              <td className={`px-2 py-2 text-right ${bet.pnl_net == null ? 'text-dim' : pnlColor(bet.pnl_net)}`}>
                {bet.pnl_net == null ? '—' : signed(bet.pnl_net)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {bets.length > shown.length && (
        <button type="button" onClick={() => setLimit(limit + PAGE_SIZE)}
          className="mt-2 min-h-11 w-full rounded-md text-[11px] text-muted ring-1 ring-edge transition-colors hover:text-ink">
          Afficher plus ({bets.length - shown.length} restants)
        </button>
      )}
    </div>
  )
}
