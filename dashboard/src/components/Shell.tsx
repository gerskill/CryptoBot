import { useEffect, useState } from 'react'
import { motion } from 'framer-motion'
import { useStore } from '../lib/store'
import { usd, pct, pnlColor } from '../lib/format'
import { usePulse } from '../lib/useLiveState'

/** Compte à rebours jusqu'au prochain scan. */
function ScanClock() {
  const nextScanAt = useStore((s) => s.state.next_scan_at)
  const [remaining, setRemaining] = useState(0)

  useEffect(() => {
    const tick = () => setRemaining(Math.max(0, (nextScanAt ?? 0) - Date.now() / 1000))
    tick()
    const id = setInterval(tick, 500)
    return () => clearInterval(id)
  }, [nextScanAt])

  if (!nextScanAt) return null
  return (
    <div className="flex items-baseline gap-2 font-mono text-sm">
      <span className="text-dim">prochain scan</span>
      <span className="text-ink tabular-nums">{remaining.toFixed(0)}s</span>
    </div>
  )
}

/** Le point de vie : distingue « marché calme » de « bot mort ». */
function Heartbeat() {
  const online = useStore((s) => s.state.bot_online)
  const connected = useStore((s) => s.connected)
  const reason = useStore((s) => s.state.reason)

  const label = !connected ? 'API injoignable' : online ? 'en ligne' : (reason ?? 'hors ligne')
  const color = !connected ? 'bg-warn' : online ? 'bg-toxic' : 'bg-blood'

  return (
    <div className="flex items-center gap-2" title={label}>
      <span className="relative flex h-2 w-2">
        {online && connected && (
          <span className={`absolute inline-flex h-full w-full animate-ping rounded-full ${color} opacity-60`} />
        )}
        <span className={`relative inline-flex h-2 w-2 rounded-full ${color}`} />
      </span>
      <span className="text-xs text-dim">{label}</span>
    </div>
  )
}

export function Shell({ children }: { children: React.ReactNode }) {
  const stats = useStore((s) => s.state.stats)
  const mode = useStore((s) => s.state.mode ?? 'PAPER')
  const paused = useStore((s) => s.state.paused)
  const cycle = useStore((s) => s.state.cycle)
  const equityPulse = usePulse(stats?.equity)

  const equity = stats?.equity ?? 0
  const pnl = stats?.total_pnl_usd ?? 0
  // Rendement rapporté à la mise de départ, pas à une valeur dérivée.
  const baseline = stats?.baseline ?? 0
  const pnlPct = baseline > 0 ? (100 * pnl) / baseline : 0

  // Night watch : entre 00h et 06h, le marché est creux.
  const hour = new Date().getHours()
  const nightWatch = hour >= 0 && hour < 6

  return (
    <div className="min-h-full">
      <header className="sticky top-0 z-20 border-b border-edge/60 bg-void/80 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1800px] flex-wrap items-end justify-between gap-4 px-5 py-3">
          <div className="flex items-end gap-6">
            <div>
              <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.2em] text-dim">
                Alpha Loop
                <span className={`rounded px-1.5 py-px text-[10px] font-semibold tracking-normal ${
                  mode === 'PAPER' ? 'bg-toxic/15 text-toxic' : 'bg-warn/15 text-warn'
                }`}>
                  {mode}
                </span>
                {paused && (
                  <span className="rounded bg-blood/15 px-1.5 py-px text-[10px] font-semibold tracking-normal text-blood">
                    EN PAUSE
                  </span>
                )}
              </div>
              {/* Le seul chiffre héroïque de l'écran. Tout le reste recule
                  d'un cran : sans ce contraste d'échelle, l'équité se noie
                  dans la densité des panneaux. */}
              <div className="mt-1 flex items-baseline gap-3">
                <span
                  className={`font-mono text-[clamp(2.25rem,1.2rem+3vw,3.5rem)] font-semibold leading-[0.9] tracking-tight ${equityPulse}`}
                >
                  {usd(equity, 2)}
                </span>
                <span className={`font-mono text-[13px] ${pnlColor(pnl)}`}>
                  {pnl >= 0 ? '+' : ''}{usd(pnl, 2)}
                  <span className="ml-1.5 text-dim">·</span>{' '}
                  <span className={pnlColor(pnl)}>{pct(pnlPct)}</span>
                </span>
              </div>
            </div>
          </div>

          {/* Rail d'état : tout ce qui n'est pas une décision, à voix basse. */}
          <div className="flex items-center gap-4 pb-1.5 text-[11px]">
            {nightWatch && (
              <span className="rounded-full bg-gem/10 px-2.5 py-1 text-[10px] text-gem ring-1 ring-gem/25">
                Night Watch
              </span>
            )}
            <ScanClock />
            <span className="font-mono text-dim">cycle {cycle ?? '—'}</span>
            <Heartbeat />
          </div>
        </div>
      </header>

      <motion.main
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        className="mx-auto max-w-[1800px] px-5 py-5"
      >
        {children}
      </motion.main>
    </div>
  )
}
