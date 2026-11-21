import { useStore } from '../../lib/store'
import { usePolled } from '../../lib/usePrediction'
import type { PredictionSummary } from '../../lib/predictionTypes'
import { utc } from '../../lib/format'

/**
 * En-tête de l'espace Liquid. Volontairement SANS chiffre de P&L global : les
 * bras ne partagent pas de capital, un total n'aurait pas de sens (et aucun
 * pari n'est réel).
 */
export function LiquidHero() {
  const demo = useStore((s) => s.liquidDemo)
  const { data } = usePolled<PredictionSummary>(`/api/prediction/summary${demo ? '?demo=true' : ''}`, 15_000)
  const arms = data?.arms ?? []
  const markets = Object.values(data?.marches_par_domaine ?? {}).reduce((a, b) => a + b, 0)
  const open = arms.reduce((sum, a) => sum + a.ouverts, 0)
  const settled = arms.reduce((sum, a) => sum + a.stats.paris_regles, 0)
  const online = data?.runner_online ?? false

  const cells: { label: string; value: string }[] = [
    { label: 'marchés suivis', value: data ? String(markets) : '—' },
    { label: 'bras actifs', value: data ? String(arms.filter((a) => a.enabled).length) : '—' },
    { label: 'paris ouverts, tous bras', value: data ? String(open) : '—' },
    { label: 'paris réglés, tous bras', value: data ? String(settled) : '—' },
  ]

  return (
    <div className="mx-auto flex max-w-[1800px] flex-wrap items-end justify-between gap-4 px-5 py-3">
      <div>
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.2em] text-dim">
          Liquid
          <span className="rounded bg-toxic/15 px-1.5 py-px text-[11px] font-semibold tracking-normal text-toxic">
            PAPER
          </span>
          {demo && (
            <span className="rounded bg-warn/15 px-1.5 py-px text-[11px] font-semibold tracking-normal text-warn">
              EXEMPLE FICTIF
            </span>
          )}
        </div>
        <div className="mt-2 flex flex-wrap gap-x-8 gap-y-2">
          {cells.map((cell) => (
            <div key={cell.label}>
              <div className="font-mono text-[1.75rem] font-semibold leading-none tracking-tight text-ink tabular-nums">
                {cell.value}
              </div>
              <div className="mt-1 text-[11px] uppercase tracking-wider text-dim">{cell.label}</div>
            </div>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-2 pb-1.5 text-[11px]" title={data?.updated_at_iso ?? ''}>
        <span className={`inline-block h-2 w-2 rounded-full ${online ? 'bg-toxic' : 'bg-blood'}`} />
        <span className="text-dim">
          {online ? 'simulation en marche' : 'simulation arrêtée'}
          {data?.updated_at_iso ? ` · dernier cycle ${utc(data.updated_at_iso)}` : ''}
        </span>
      </div>
    </div>
  )
}
