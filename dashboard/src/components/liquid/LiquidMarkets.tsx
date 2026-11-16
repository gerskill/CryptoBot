import { useState } from 'react'
import { Panel, Empty } from '../Panel'
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
  return [...rows.values()].sort((a, b) => (a.when ?? '9').localeCompare(b.when ?? '9'))
}

export function LiquidMarkets({ demo }: { demo: boolean }) {
  const [famille, setFamille] = useState<string>('prix')
  const [query, setQuery] = useState('')
  const { data, error } = usePolled<Catalogue>(`/api/prediction/markets${demo ? '?demo=true' : ''}`, 30_000)

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

  const needle = query.trim().toLowerCase()
  const inFamille = data.markets.filter((m) => m.famille === famille)
  const shown = needle ? inFamille.filter((m) => m.title.toLowerCase().includes(needle)) : inFamille
  const domains = [...new Set(shown.map((m) => m.domain))]

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1.5">
          {MARKET_FAMILLES.map((id) => {
            const count = data.markets.filter((m) => m.famille === id).length
            if (id === 'autre' && count === 0) return null
            const active = id === famille
            return (
              <button key={id} onClick={() => setFamille(id)}
                className={`rounded-md px-3 py-1.5 text-[11px] ring-1 transition-colors ${active ? 'bg-tide/10 text-tide ring-tide/40' : 'text-muted ring-edge hover:text-ink'}`}>
                {FAMILLE_LABEL[id]} <span className="ml-1 font-mono text-[10px] opacity-70">{count}</span>
              </button>
            )
          })}
        </div>
        <label className="flex items-center gap-2 text-[11px] text-dim">
          Chercher
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="BTC, Arsenal, NVDA…"
            className="w-44 rounded-md bg-surface px-2.5 py-1.5 text-[11px] text-ink ring-1 ring-edge placeholder:text-dim focus:outline-none focus:ring-tide/50" />
        </label>
      </div>
      <p className="mb-3 text-[11px] text-muted">
        Prix relevés le {utc(data.updated_at_iso)}. Le pourcentage est le prix milieu du côté affiché, pas un
        prix auquel on peut forcément acheter.
      </p>

      {domains.length === 0 && <Empty>Aucun marché ne correspond.</Empty>}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {domains.map((domain) => {
          const rows = group(shown.filter((m) => m.domain === domain))
          return (
            <Panel key={domain} title={DOMAIN_LABEL[domain] ?? domain} count={rows.length}>
              <div className="max-h-[520px] overflow-auto">
                <table className="w-full min-w-[460px] text-left text-[11px]">
                  <thead className="sticky top-0 bg-surface text-[10px] uppercase tracking-wider text-dim">
                    <tr>
                      <th className="px-2 py-1.5 font-medium">Marché</th>
                      <th className="px-2 py-1.5 font-medium">Côté</th>
                      <th className="px-2 py-1.5 text-right font-medium">Probabilité</th>
                      <th className="px-2 py-1.5 font-medium">Date (UTC)</th>
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
                              <span className="ml-1.5 font-mono text-[9px] uppercase text-dim">{row.venue}</span>
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
        })}
      </div>
    </div>
  )
}
