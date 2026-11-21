import type { PredictionBet, PricePoint } from '../../lib/predictionTypes'
import { utc, prob } from '../../lib/format'

const W = 640
const H = 240
const PAD = { left: 40, right: 14, top: 14, bottom: 26 }

/**
 * Graphique du prix d'un pari, avec l'entrée et le règlement posés DESSUS.
 *
 * Les deux marqueurs lisent `bet.entry_time` / `bet.entry_price` et
 * `bet.settle_time` / `bet.settle_value` — les mêmes champs que la ligne du
 * tableau. L'axe horizontal est le temps réel, pas le rang du point : un
 * marqueur tombe donc à sa date exacte même si la série a des trous.
 */
export function BetChart({ bet, points }: { bet: PredictionBet; points: PricePoint[] }) {
  const series = points
    .map((p) => ({ x: new Date(p.t).getTime(), y: p.mid }))
    .filter((p) => !Number.isNaN(p.x))
  const entryX = new Date(bet.entry_time).getTime()
  const settled = bet.status === 'regle' && bet.settle_time && bet.settle_value != null
  const settleX = settled ? new Date(bet.settle_time as string).getTime() : null

  const xs = [...series.map((p) => p.x), entryX, ...(settleX != null ? [settleX] : [])]
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs)
  const spanX = maxX - minX || 1

  const px = (x: number) => PAD.left + ((x - minX) / spanX) * (W - PAD.left - PAD.right)
  const py = (y: number) => PAD.top + (1 - y) * (H - PAD.top - PAD.bottom)

  const path = series
    .map((p, i) => `${i === 0 ? 'M' : 'L'}${px(p.x).toFixed(1)},${py(p.y).toFixed(1)}`)
    .join(' ')

  // Point de la série le plus proche de l'entrée : sert au contrôle affiché
  // sous le graphique, pour voir d'un coup d'œil si les deux sources collent.
  const nearest = series.length
    ? series.reduce((best, p) => (Math.abs(p.x - entryX) < Math.abs(best.x - entryX) ? p : best))
    : null

  const settleColor = bet.won ? 'var(--color-toxic)' : 'var(--color-blood)'

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img"
        aria-label={`Prix du pari ${bet.title}, entrée à ${prob(bet.entry_price)}`}>
        {[0, 0.25, 0.5, 0.75, 1].map((level) => (
          <g key={level}>
            <line x1={PAD.left} x2={W - PAD.right} y1={py(level)} y2={py(level)}
              stroke="var(--color-edge)" strokeWidth="1" />
            <text x={PAD.left - 6} y={py(level) + 3} textAnchor="end"
              className="fill-dim font-mono text-[11px]">{level * 100}%</text>
          </g>
        ))}
        {/* Niveau du prix d'entrée, sur toute la largeur. */}
        <line x1={PAD.left} x2={W - PAD.right} y1={py(bet.entry_price)} y2={py(bet.entry_price)}
          stroke="var(--color-gem)" strokeWidth="1" strokeDasharray="4 3" opacity="0.6" />
        {path && <path d={path} fill="none" stroke="var(--color-ink)" strokeWidth="1.5" />}
        <circle cx={px(entryX)} cy={py(bet.entry_price)} r="5" fill="var(--color-gem)"
          stroke="var(--color-void)" strokeWidth="1.5" />
        {settleX != null && (
          <circle cx={px(settleX)} cy={py(bet.settle_value as number)} r="5" fill={settleColor}
            stroke="var(--color-void)" strokeWidth="1.5" />
        )}
        <text x={PAD.left} y={H - 8} className="fill-dim font-mono text-[11px]">
          {utc(new Date(minX).toISOString())}
        </text>
        <text x={W - PAD.right} y={H - 8} textAnchor="end" className="fill-dim font-mono text-[11px]">
          {utc(new Date(maxX).toISOString())}
        </text>
      </svg>

      <dl className="mt-3 grid gap-x-6 gap-y-2 text-[11px] sm:grid-cols-2">
        <div>
          <dt className="flex items-center gap-1.5 text-dim">
            <span className="inline-block h-2 w-2 rounded-full bg-gem" /> Entrée (tableau et graphique)
          </dt>
          <dd className="mt-0.5 font-mono text-ink">{utc(bet.entry_time)} · {prob(bet.entry_price)}</dd>
        </div>
        <div>
          <dt className="flex items-center gap-1.5 text-dim">
            <span className="inline-block h-2 w-2 rounded-full"
              style={{ background: settled ? settleColor : 'var(--color-dim)' }} /> Règlement
          </dt>
          <dd className="mt-0.5 font-mono text-ink">
            {settled
              ? `${utc(bet.settle_time)} · ${prob(bet.settle_value)}`
              : bet.status === 'indetermine' ? 'indéterminé — issue non établie' : 'pas encore réglé'}
          </dd>
        </div>
        <div className="sm:col-span-2">
          <dt className="text-dim">Contrôle : point de la série de prix le plus proche de l'entrée</dt>
          <dd className="mt-0.5 font-mono text-muted">
            {nearest
              ? `${utc(new Date(nearest.x).toISOString())} · milieu ${prob(nearest.y)} · écart de temps ${Math.round(Math.abs(nearest.x - entryX) / 1000)} s`
              : 'aucune série de prix enregistrée pour cette pièce'}
          </dd>
        </div>
      </dl>
    </div>
  )
}
