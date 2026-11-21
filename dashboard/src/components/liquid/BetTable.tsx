import { Empty } from '../Panel'
import { NoResults } from '../explore/ExploreBar'
import { SortableTh } from '../explore/SortControls'
import { rowProps } from '../explore/useDrawer'
import { pnlColor, utc, prob } from '../../lib/format'
import type { PredictionBet } from '../../lib/predictionTypes'
import { signed } from './liquidLabels'
import { issueLabel } from './betSort'

type Handler = Parameters<typeof rowProps>[0]

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
  if (total === 0) return <Empty>Aucun pari pour ce bras.</Empty>
  if (bets.length === 0) return <NoResults query={query} />
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
          {bets.map((bet) => (
            <tr key={bet.id} {...rowProps(onOpen(bet.id))}
              className={`cursor-pointer border-t border-edge/40 transition-colors hover:bg-raised ${bet.id === selectedId ? 'bg-raised' : ''}`}>
              <td className="whitespace-nowrap px-2 py-3 text-muted sm:py-2">{utc(bet.entry_time)}</td>
              <td className="whitespace-normal px-2 py-3 font-sans text-ink sm:py-2">{bet.title}</td>
              <td className="px-2 py-3 text-muted sm:py-2">{bet.side_name}</td>
              <td className="px-2 py-3 text-right text-ink sm:py-2">{prob(bet.entry_price)}</td>
              <td className="px-2 py-3 text-right text-muted sm:py-2">{prob(bet.prob_estimee)}</td>
              <td className="px-2 py-3 text-right text-muted sm:py-2">{issueLabel(bet)}</td>
              <td className={`px-2 py-3 text-right sm:py-2 ${bet.pnl_net == null ? 'text-dim' : pnlColor(bet.pnl_net)}`}>
                {bet.pnl_net == null ? '—' : signed(bet.pnl_net)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
