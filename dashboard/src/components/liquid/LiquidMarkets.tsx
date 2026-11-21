import { useMemo } from 'react'
import { useFilter } from '../../lib/useFilter'
import { Panel, Empty } from '../Panel'
import { EMPTY_ARRAY } from '../../lib/empty'
import { useExplore } from '../../lib/useExplore'
import { ExploreBar, NoResults } from '../explore/ExploreBar'
import { SortableTh } from '../explore/SortControls'
import { usePolled } from '../../lib/usePrediction'
import type { Catalogue, CatalogueMarket } from '../../lib/predictionTypes'
import { utc, prob } from '../../lib/format'
import { DOMAIN_LABEL, MARKET_FAMILLES } from './liquidLabels'

const FAMILLE_LABEL: Record<string, string> = {
  prix: 'Prix', sport: 'Sport', questions: 'Questions', autre: 'Non classé',
}

/** Barre de probabilité du premier côté. Rien si le prix est absent. */
function ProbBar({ value }: { value: number | null }) {
  if (value == null) return <span className="text-dim">—</span>
  return (
    <span className="flex items-center justify-end gap-2">
      <span className="h-1 w-16 overflow-hidden rounded-full bg-edge">
        <span className="block h-full bg-tide" style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} />
      </span>
      <span className="w-12 text-right text-ink">{prob(value)}</span>
    </span>
  )
}

type Row = { key: string; title: string; when: string | null; venue: string | null; items: CatalogueMarket[] }

/** Les issues d'une même question sont regroupées sous leur question. */
function group(markets: CatalogueMarket[]): Row[] {
  const rows = new Map<string, Row>()
  for (const m of markets) {
    const key = m.question ? `q${m.question}` : `o${m.outcome}`
    const row = rows.get(key) ?? {
      key, title: m.question ? (m.group ?? m.title) : m.title,
      when: m.start ?? m.expiry, venue: m.venue, items: [],
    }
    row.items.push(m)
    rows.set(key, row)
  }
  // L'ordre d'arrivée est conservé : date par défaut, ou le tri choisi.
  return [...rows.values()]
}

const whenOf = (m: CatalogueMarket) => m.start ?? m.expiry
const DEFAULT_FAMILLE = 'prix'
const DESC_FIRST = ['prob']
const SEARCH_OF = (m: CatalogueMarket) => [m.title, m.group, m.label, m.venue, m.underlying, DOMAIN_LABEL[m.domain]]
const ACCESSORS = {
  marche: (m: CatalogueMarket) => m.question ? (m.group ?? m.title) : m.title,
  prob: (m: CatalogueMarket) => m.sides[0]?.mid,
  date: whenOf,
}
const byDate = (a: CatalogueMarket, b: CatalogueMarket) => (whenOf(a) ?? '9').localeCompare(whenOf(b) ?? '9')

type Explore = ReturnType<typeof useExplore<CatalogueMarket>>

/** Un tableau par domaine ; l'en-tête trie, les lignes gardent l'ordre reçu. */
function DomainPanel({ domain, markets, explore }: {
  domain: string; markets: CatalogueMarket[]; explore: Explore
}) {
  const rows = group(markets)
  return (
    <Panel title={DOMAIN_LABEL[domain] ?? domain} count={rows.length}>
      <div className="max-h-[520px] overflow-auto">
        <table className="w-full min-w-[460px] text-left text-[11px]">
          <thead className="sticky top-0 bg-surface text-[11px] uppercase tracking-wider text-dim">
            <tr>
              <SortableTh label="Marché" sortKey="marche" ariaSort={explore.ariaSort('marche')} onSort={explore.toggleSort} />
              <th className="px-2 py-1.5 font-medium">Côté</th>
              <SortableTh label="Probabilité" sortKey="prob" align="right" ariaSort={explore.ariaSort('prob')} onSort={explore.toggleSort} />
              <SortableTh label="Date (UTC)" sortKey="date" ariaSort={explore.ariaSort('date')} onSort={explore.toggleSort} />
            </tr>
          </thead>
          <tbody className="font-mono tabular-nums">
            {rows.map((row) => row.items.map((m, index) => {
              const single = row.items.length === 1
              // Un match à deux côtés (NBA) montre les deux équipes.
              const sides = single && m.sides[0]?.name !== 'Yes' ? m.sides : m.sides.slice(0, 1)
              return sides.map((side, s) => (
                <tr key={side.coin} className={index === 0 && s === 0 ? 'border-t border-edge/40' : ''}>
                  <td className="px-2 py-1.5 font-sans text-ink">
                    {index === 0 && s === 0 ? row.title : ''}
                    {index === 0 && s === 0 && row.venue && (
                      <span className="ml-1.5 font-mono text-[11px] uppercase text-dim">{row.venue}</span>
                    )}
                  </td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-muted">
                    {single ? (side.name === 'Yes' ? 'Oui' : side.name) : (m.label ?? '—')}
                  </td>
                  <td className="px-2 py-1.5"><ProbBar value={side.mid} /></td>
                  <td className="whitespace-nowrap px-2 py-1.5 text-dim">
                    {index === 0 && s === 0 ? utc(row.when) : ''}
                  </td>
                </tr>
              ))
            }))}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function FamilleTabs({ markets, famille, onChange }: {
  markets: CatalogueMarket[]; famille: string; onChange: (id: string) => void
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {MARKET_FAMILLES.map((id) => {
        const count = markets.filter((m) => m.famille === id).length
        if (id === 'autre' && count === 0) return null
        const active = id === famille
        return (
          <button key={id} type="button" aria-pressed={active} onClick={() => onChange(id)}
            className={`min-h-11 rounded-md px-3 py-1.5 text-[11px] ring-1 transition-colors ${active ? 'bg-tide/10 text-tide ring-tide/40' : 'text-muted ring-edge hover:text-ink'}`}>
            {FAMILLE_LABEL[id]} <span className="ml-1 font-mono text-[11px] opacity-70">{count}</span>
          </button>
        )
      })}
    </div>
  )
}

export function LiquidMarkets({ demo }: { demo: boolean }) {
  const [familleRaw, setFamille] = useFilter('famille', DEFAULT_FAMILLE)
  const [query, setQuery] = useFilter('q', '')
  // Une URL bricolée ne doit pas produire une famille inconnue.
  const famille = (MARKET_FAMILLES as readonly string[]).includes(familleRaw) ? familleRaw : DEFAULT_FAMILLE
  const { data, error } = usePolled<Catalogue>(`/api/prediction/markets${demo ? '?demo=true' : ''}`, 30_000)
  const inFamille = useMemo(
    () => (data?.markets ?? EMPTY_ARRAY).filter((m) => m.famille === famille).sort(byDate),
    [data, famille],
  )
  const explore = useExplore({
    rows: inFamille,
    searchOf: SEARCH_OF,
    accessors: ACCESSORS,
    filterActive: famille !== DEFAULT_FAMILLE,
    onReset: () => setFamille(DEFAULT_FAMILLE),
    descFirst: DESC_FIRST,
    query,
    onQuery: setQuery,
  })

  if (!data) return <Empty>{error ? `Catalogue illisible (${error}).` : 'Chargement…'}</Empty>
  if (data.total === 0) {
    return (
      <Empty>
        {demo
          ? 'L’exemple fictif ne contient pas de catalogue de marchés.'
          : 'Aucun marché relevé : la simulation n’a pas encore tourné. Lancer python3 scripts/run_prediction.py'}
      </Empty>
    )
  }

  const shown = explore.visible
  const domains = [...new Set(shown.map((m) => m.domain))]

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <FamilleTabs markets={data.markets} famille={famille} onChange={setFamille} />
        <ExploreBar label="Chercher un marché" placeholder="BTC, Arsenal, NVDA…"
          query={explore.query} onQuery={explore.setQuery} shown={shown.length}
          total={explore.total} isFiltered={explore.isFiltered} onReset={explore.reset} />
      </div>
      <p className="mb-3 text-[11px] text-muted">
        Prix relevés le {utc(data.updated_at_iso)}. Le pourcentage est le prix milieu du côté affiché, pas un
        prix auquel on peut forcément acheter.
      </p>

      {domains.length === 0 && <NoResults query={explore.query} />}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {domains.map((domain) => (
          <DomainPanel key={domain} domain={domain} markets={shown.filter((m) => m.domain === domain)} explore={explore} />
        ))}
      </div>
    </div>
  )
}
