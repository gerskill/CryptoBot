import { useState } from 'react'
import { Panel, Empty } from '../Panel'
import { usePolled } from '../../lib/usePrediction'
import type { Perps } from '../../lib/predictionTypes'
import { compact, pct, pnlColor, usd, utc } from '../../lib/format'

const DEX_LABEL: Record<string, string> = {
  principal: 'Crypto',
  xyz: 'Actions, indices, matières premières',
}

const money = (value: number) => (value >= 100 ? usd(value, 2) : value >= 1 ? usd(value, 3) : usd(value, 5))

/** Marchés à terme de Liquid. Lecture seule : aucun bras ne les trade. */
export function LiquidPerps() {
  const [dex, setDex] = useState('principal')
  const { data, error } = usePolled<Perps>('/api/liquid/perps', 30_000)

  if (!data) return <Empty>{error ? `Marchés illisibles (${error}).` : 'Chargement…'}</Empty>
  if (data.total === 0) {
    return <Empty>Aucun marché relevé : la simulation n’a pas encore tourné. Lancer python3 scripts/run_prediction.py</Empty>
  }
  const dexes = [...new Set(data.markets.map((m) => m.dex))]
  const rows = data.markets.filter((m) => m.dex === dex)

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap gap-1.5">
          {dexes.map((id) => (
            <button key={id} onClick={() => setDex(id)}
              className={`rounded-md px-3 py-1.5 text-[11px] ring-1 transition-colors ${id === dex ? 'bg-tide/10 text-tide ring-tide/40' : 'text-muted ring-edge hover:text-ink'}`}>
              {DEX_LABEL[id] ?? id}
              <span className="ml-1 font-mono text-[11px] opacity-70">{data.markets.filter((m) => m.dex === id).length}</span>
            </button>
          ))}
        </div>
        <span className="text-[11px] text-muted">Relevé du {utc(data.updated_at_iso)}</span>
      </div>
      <Panel title="Marchés à terme (perpétuels)" count={rows.length}
        hint="Affichage seul. Aucun bras ne trade ces marchés : c’est le prix des sous-jacents sur lesquels portent les paris de seuil. Classés par volume sur 24 h, les 120 premiers toutes bourses confondues.">
        <div className="max-h-[640px] overflow-auto">
          <table className="w-full min-w-[640px] whitespace-nowrap text-left text-[11px]">
            <thead className="sticky top-0 bg-surface text-[11px] uppercase tracking-wider text-dim">
              <tr>
                <th className="px-2 py-1.5 font-medium">Marché</th>
                <th className="px-2 py-1.5 text-right font-medium">Prix</th>
                <th className="px-2 py-1.5 text-right font-medium">24 h</th>
                <th className="px-2 py-1.5 text-right font-medium">Volume 24 h</th>
                <th className="px-2 py-1.5 text-right font-medium">Positions ouvertes</th>
                <th className="px-2 py-1.5 text-right font-medium">Financement / 8 h</th>
                <th className="px-2 py-1.5 text-right font-medium">Levier max</th>
              </tr>
            </thead>
            <tbody className="font-mono tabular-nums">
              {rows.map((m) => (
                <tr key={m.coin} className="border-t border-edge/40">
                  <td className="px-2 py-1.5 text-ink">{m.symbol}</td>
                  <td className="px-2 py-1.5 text-right text-ink">{money(m.mark)}</td>
                  <td className={`px-2 py-1.5 text-right ${m.change_24h_pct == null ? 'text-dim' : pnlColor(m.change_24h_pct)}`}>
                    {m.change_24h_pct == null ? '—' : pct(m.change_24h_pct, 2)}
                  </td>
                  <td className="px-2 py-1.5 text-right text-muted">
                    {m.volume_24h_usd == null ? '—' : `$${compact(m.volume_24h_usd)}`}
                  </td>
                  <td className="px-2 py-1.5 text-right text-muted">
                    {m.open_interest_usd == null ? '—' : `$${compact(m.open_interest_usd)}`}
                  </td>
                  <td className="px-2 py-1.5 text-right text-muted">
                    {m.funding_8h_pct == null ? '—' : `${m.funding_8h_pct.toFixed(4)} %`}
                  </td>
                  <td className="px-2 py-1.5 text-right text-dim">{m.max_leverage == null ? '—' : `${m.max_leverage}x`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}
