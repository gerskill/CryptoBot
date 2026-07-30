import { motion } from 'framer-motion'
import { useStore } from '../lib/store'
import { EMPTY_ARRAY, EMPTY_OBJECT } from '../lib/empty'
import { Panel, NoSource } from './Panel'
import { usd, pct, duration, pnlColor } from '../lib/format'

/** Courbe d'equity reconstruite depuis le journal, du plus ancien au plus récent. */
function EquityCurve() {
  const trades = useStore((s) => s.trades)
  const initial = useStore((s) => {
    const stats = s.state.stats
    return stats ? stats.equity - stats.total_pnl_usd : 1000
  })

  if (trades.length < 2) {
    return (
      <p className="px-1 py-3 text-[11px] leading-snug text-dim">
        Courbe disponible à partir de 2 trades clôturés ({trades.length} pour l'instant).
      </p>
    )
  }

  const chronological = [...trades].reverse()
  let running = initial
  const points = [initial, ...chronological.map((t) => (running += t.pnl_usd))]
  const min = Math.min(...points)
  const max = Math.max(...points)
  const span = max - min || 1
  const path = points
    .map((value, index) => {
      const x = (index / (points.length - 1)) * 100
      const y = 100 - ((value - min) / span) * 100
      return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`
    })
    .join(' ')

  const up = points[points.length - 1] >= points[0]

  return (
    <div className="px-1 py-2">
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="h-16 w-full">
        <motion.path
          d={path}
          fill="none"
          stroke={up ? 'var(--color-toxic)' : 'var(--color-blood)'}
          strokeWidth="1.5"
          vectorEffect="non-scaling-stroke"
          initial={{ pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 0.6 }}
        />
      </svg>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-dim tabular-nums">
        <span>{usd(points[0], 0)}</span>
        <span className={pnlColor(points[points.length - 1] - points[0])}>
          {usd(points[points.length - 1], 2)}
        </span>
      </div>
    </div>
  )
}

function StatGrid() {
  const stats = useStore((s) => s.state.stats)
  const live = useStore((s) => s.state.live_allowed)
  if (!stats) return null

  const cells = [
    { label: 'win rate', value: `${stats.win_rate}%` },
    { label: 'profit factor', value: stats.profit_factor.toFixed(2) },
    { label: 'trades', value: String(stats.total_trades) },
    { label: 'drawdown max', value: `${stats.max_drawdown_pct}%` },
  ]

  return (
    <>
      <dl className="grid grid-cols-2 gap-2">
        {cells.map((cell) => (
          <div key={cell.label} className="rounded-lg border border-edge/60 px-3 py-2">
            <dt className="text-[9px] uppercase tracking-wide text-dim">{cell.label}</dt>
            <dd className="font-mono text-lg tabular-nums">{cell.value}</dd>
          </div>
        ))}
      </dl>
      {live && (
        <div
          className={`mt-2 rounded-lg border px-3 py-2 text-[11px] leading-snug ${
            live[0] ? 'border-warn/40 bg-warn/10 text-warn' : 'border-edge/60 text-dim'
          }`}
        >
          <span className="font-medium">Passage en LIVE : {live[0] ? 'autorisé' : 'bloqué'}</span>
          <br />
          {live[1]}
        </div>
      )}
    </>
  )
}

/** Ghost Mode : ce que le bot a refusé et qui serait parti. */
function GhostMode() {
  const shadow = useStore((s) => s.shadow)
  const byFamily = useStore((s) => s.state.shadow_by_family ?? EMPTY_OBJECT)
  const tracked = useStore((s) => s.state.shadow?.tracked ?? 0)

  const families = Object.entries(byFamily).sort((a, b) => b[1].missed_rate - a[1].missed_rate)

  return (
    <div className="rounded-lg border border-edge/60 px-3 py-2.5">
      <div className="flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-wide text-dim">Ghost mode</span>
        <span className="font-mono text-[10px] text-dim tabular-nums">
          {tracked} en cours · {shadow.total} jugés
        </span>
      </div>

      {shadow.total === 0 ? (
        <p className="mt-1.5 text-[11px] leading-snug text-dim">
          Les rejets sont suivis 4 h pour mesurer ce qu'ils seraient devenus.
          Premier verdict après ce délai.
        </p>
      ) : (
        <>
          <div className="mt-1.5 font-mono text-2xl tabular-nums">
            <span className={shadow.missed_rate > 25 ? 'text-warn' : 'text-ink'}>
              {shadow.missed_rate}%
            </span>
            <span className="ml-2 text-[11px] text-dim">des rejets seraient partis à +100%</span>
          </div>
          {families.length > 0 && (
            <ul className="mt-2 space-y-1">
              {families.map(([family, stat]) => (
                <li key={family} className="flex justify-between font-mono text-[10px] tabular-nums">
                  <span className="text-dim">{family}</span>
                  <span className={stat.missed_rate > 25 ? 'text-warn' : 'text-dim'}>
                    {stat.missed_rate}% · n={stat.sample}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

/** Derniers ajustements de paramètres décidés par le bot. */
function LearningFeed() {
  const history = useStore(
    (s) => (s.state.params?.learning?.parameter_adjustment_history ?? EMPTY_ARRAY) as any[],
  )
  // Le tri se fait APRÈS le sélecteur : trier dedans créerait un tableau neuf
  // à chaque rendu et relancerait la boucle.
  const recent = [...history].reverse().slice(0, 4)

  if (recent.length === 0) {
    return (
      <NoSource
        label="Aucun ajustement pour l'instant"
        why="Le bot n'ajuste qu'à partir de 10 échantillons dans un segment, et valide par backtest."
      />
    )
  }

  return (
    <ul className="space-y-2">
      {recent.map((entry, index) => (
        <li key={index} className="rounded-lg border border-gem/25 bg-gem/[0.04] px-3 py-2">
          <div className="font-mono text-[11px] text-gem">
            {entry.param_name} : {String(entry.old_value)} → {String(entry.new_value)}
          </div>
          <div className="mt-0.5 text-[10px] leading-snug text-dim">{entry.reason}</div>
        </li>
      ))}
    </ul>
  )
}

function RecentTrades() {
  const allTrades = useStore((s) => s.trades)
  const trades = allTrades.slice(0, 5)
  if (trades.length === 0) {
    return <p className="text-[11px] text-dim">Aucun trade clôturé.</p>
  }
  return (
    <ul className="space-y-1">
      {trades.map((trade) => (
        <li
          key={trade.id}
          className="flex items-baseline justify-between gap-2 rounded-lg border border-edge/50 px-3 py-1.5"
        >
          <span className="truncate text-xs font-medium">{trade.token}</span>
          <span className="truncate font-mono text-[10px] text-dim">{trade.exit_reason}</span>
          <span className={`shrink-0 font-mono text-xs tabular-nums ${pnlColor(trade.pnl_pct)}`}>
            {pct(trade.pnl_pct)}
          </span>
          <span className="shrink-0 font-mono text-[10px] text-dim tabular-nums">
            {duration(trade.duration_min)}
          </span>
        </li>
      ))}
    </ul>
  )
}

export function TheBrain() {
  const apis = useStore((s) => s.state.api_status ?? EMPTY_OBJECT)
  const offline = Object.entries(apis).filter(([, ok]) => !ok).map(([name]) => name)

  return (
    <Panel title="The Brain">
      <StatGrid />
      <div className="mt-3"><EquityCurve /></div>

      <h3 className="mb-1.5 mt-3 text-[10px] uppercase tracking-wide text-dim">Apprentissage</h3>
      <LearningFeed />

      <h3 className="mb-1.5 mt-4 text-[10px] uppercase tracking-wide text-dim">Derniers trades</h3>
      <RecentTrades />

      <h3 className="mb-1.5 mt-4 text-[10px] uppercase tracking-wide text-dim">Ce que le bot a raté</h3>
      <GhostMode />

      <h3 className="mb-1.5 mt-4 text-[10px] uppercase tracking-wide text-dim">Sources absentes</h3>
      <div className="space-y-2">
        <NoSource
          label="Smart money"
          why="GMGN n'expose aucune API publique. Le filtre est inactif et le composant exclu du score."
        />
        <NoSource
          label="BubbleMap"
          why="Non intégré. La concentration est approchée par le top holder et le top 10."
        />
        <NoSource
          label="Test honeypot"
          why="Demande un wallet et un swap réel. Impossible en mode papier."
        />
        {offline.length > 0 && (
          <NoSource label="API sans clé" why={offline.join(', ')} />
        )}
      </div>
    </Panel>
  )
}
