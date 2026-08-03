import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useStore } from '../lib/store'
import { EMPTY_ARRAY } from '../lib/empty'
import { Panel } from './Panel'
import { usd, pct, pnlColor } from '../lib/format'
import type { Arm } from '../lib/types'

/**
 * Les 7 stratégies, chacune avec son entonnoir du cycle.
 *
 * Comble le trou principal du dashboard : `state.candidates` ne montrait que
 * le bras témoin, et les six autres n'existaient que dans `state.json`. Un
 * bras invisible est un bras qu'on ne débogue pas — c'est ainsi que trois
 * d'entre eux ont pu rejeter 100 % des candidats sans que ça se voie.
 */

/** Barre d'entonnoir : vu -> retenu -> qualifié. La chute EST l'information. */
function Funnel({ arm }: { arm: Arm }) {
  const seen = (arm.kept ?? 0) + (arm.rejected ?? 0)
  const kept = arm.kept ?? 0
  const qualified = arm.qualified ?? 0
  if (!seen) return <span className="text-[10px] text-dim">aucun candidat ce cycle</span>

  const keptPct = (kept / seen) * 100
  const qualPct = (qualified / seen) * 100

  return (
    <div className="flex items-center gap-2">
      <div className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-white/10">
        <motion.div
          className="absolute inset-y-0 left-0 bg-dim/50"
          initial={false}
          animate={{ width: `${keptPct}%` }}
          transition={{ duration: 0.4 }}
        />
        <motion.div
          className="absolute inset-y-0 left-0 bg-toxic"
          initial={false}
          animate={{ width: `${qualPct}%` }}
          transition={{ duration: 0.4 }}
        />
      </div>
      <span className="shrink-0 font-mono text-[10px] tabular-nums text-dim">
        {seen}→{kept}→<span className={qualified ? 'text-toxic' : ''}>{qualified}</span>
      </span>
    </div>
  )
}

// Seuils calés sur les taux d'atteinte mesurés (26 positions instrumentées) :
// +50 % de pic franchi par 23 % des trades, ce qui met la barre haute d'un
// WR/PF sain nettement au-dessus de ce qu'un memecoin filtré au hasard donne.
const WR_GOOD = 33
const WR_OK = 20
const PF_GOOD = 1.5
const PF_OK = 1.0
const PF_SCALE_MAX = 2.0

function microBarColor(value: number, good: number, ok: number): string {
  if (value >= good) return 'var(--color-toxic)'
  if (value >= ok) return 'var(--color-warn)'
  return 'var(--color-blood)'
}

/** Deux micro-barres horizontales : WR et PF, lisibles sans déplier la ligne. */
function MicroBars({ winRate, profitFactor }: { winRate: number; profitFactor: number }) {
  const wrColor = microBarColor(winRate, WR_GOOD, WR_OK)
  const pfColor = microBarColor(profitFactor, PF_GOOD, PF_OK)
  const pfFill = Math.min(profitFactor, PF_SCALE_MAX) / PF_SCALE_MAX

  return (
    <span
      className="flex shrink-0 flex-col justify-center gap-[3px]"
      title={`WR ${winRate.toFixed(0)}% · PF ${profitFactor.toFixed(2)}`}
    >
      <span className="h-[3px] w-10 overflow-hidden rounded-full bg-white/10">
        <span
          className="block h-full rounded-full transition-[width]"
          style={{ width: `${Math.min(winRate, 100)}%`, backgroundColor: wrColor }}
        />
      </span>
      <span className="h-[3px] w-10 overflow-hidden rounded-full bg-white/10">
        <span
          className="block h-full rounded-full transition-[width]"
          style={{ width: `${pfFill * 100}%`, backgroundColor: pfColor }}
        />
      </span>
    </span>
  )
}

function ArmRow({ arm, open, onToggle }: { arm: Arm; open: boolean; onToggle: () => void }) {
  const stats = arm.stats
  const pnl = stats?.total_pnl_usd ?? 0
  const trades = stats?.total_trades ?? arm.trades ?? 0
  const positions = stats?.open_positions ?? 0

  return (
    <div className="border-b border-white/5 last:border-0">
      <button
        onClick={onToggle}
        className="group w-full px-1 py-2 text-left transition-colors hover:bg-white/[0.03]"
        aria-expanded={open}
      >
        <div className="flex items-baseline gap-2">
          <span className="w-[4.5rem] shrink-0 truncate font-mono text-xs text-ink">
            {arm.name}
          </span>
          {arm.role === 'consensus' && (
            <span className="shrink-0 rounded bg-white/10 px-1 text-[9px] text-dim">
              ≥{arm.min_confluence}
            </span>
          )}
          {positions > 0 && (
            <span className="shrink-0 rounded bg-toxic/20 px-1 text-[9px] text-toxic">
              {positions} en cours
            </span>
          )}
          <span className="flex-1" />
          {stats && trades > 0 && (
            <MicroBars winRate={stats.win_rate} profitFactor={stats.profit_factor} />
          )}
          <span className="shrink-0 font-mono text-[10px] tabular-nums text-dim">
            {trades} trades
          </span>
          <span className={`w-16 shrink-0 text-right font-mono text-xs tabular-nums ${pnlColor(pnl)}`}>
            {trades ? usd(pnl) : '—'}
          </span>
        </div>
        <div className="mt-1.5 pl-[4.5rem]">
          <Funnel arm={arm} />
        </div>
      </button>

      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: 'auto', opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.18 }}
            className="overflow-hidden"
          >
            <div className="space-y-2 px-1 pb-3 pl-[4.5rem] text-[11px] leading-snug">
              {arm.description && <p className="text-dim">{arm.description}</p>}
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1 font-mono text-[10px]">
                <div className="flex justify-between">
                  <dt className="text-dim">stop loss</dt>
                  <dd className="text-blood">{arm.stop_loss_pct}%</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-dim">take profit 1</dt>
                  <dd className="text-toxic">+{arm.take_profit_1}%</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-dim">seuil alpha</dt>
                  <dd className="text-ink">{arm.entry_threshold}</dd>
                </div>
                <div className="flex justify-between">
                  <dt className="text-dim">durée max</dt>
                  <dd className="text-ink">{arm.max_hold_time_minutes} min</dd>
                </div>
                {stats && trades > 0 && (
                  <>
                    <div className="flex justify-between">
                      <dt className="text-dim">win rate</dt>
                      <dd className="text-ink">{pct(stats.win_rate)}</dd>
                    </div>
                    <div className="flex justify-between">
                      <dt className="text-dim">profit factor</dt>
                      <dd className="text-ink">{stats.profit_factor}</dd>
                    </div>
                  </>
                )}
              </dl>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}

/** Tokens sur lesquels au moins deux stratégies s'accordent. */
function Confluence() {
  const rows = useStore((s) => s.state.confluence ?? EMPTY_ARRAY)
  const agreed = rows.filter((r) => r.above_threshold_count >= 2)

  if (!rows.length) {
    return <p className="px-1 py-2 text-[11px] text-dim">Aucun candidat retenu ce cycle.</p>
  }
  if (!agreed.length) {
    return (
      <p className="px-1 py-2 text-[11px] leading-snug text-dim">
        Aucun accord. Les fenêtres d'âge et de liquidité sont disjointes par
        construction&nbsp;: un token que <span className="text-ink">sniper</span> accepte
        n'entre dans la fenêtre d'aucun autre bras.
      </p>
    )
  }
  return (
    <ul className="space-y-1 px-1 py-1">
      {agreed.slice(0, 6).map((row) => (
        <li key={row.token_address} className="flex items-baseline gap-2 text-[11px]">
          <span className="w-20 shrink-0 truncate font-mono text-ink">{row.symbol}</span>
          <span className="shrink-0 rounded bg-toxic/20 px-1 font-mono text-[9px] text-toxic">
            {row.above_threshold_count}
          </span>
          <span className="truncate text-[10px] text-dim">
            {row.above_threshold_by.join(' · ')}
          </span>
        </li>
      ))}
    </ul>
  )
}

export function TheArms() {
  const arms = useStore((s) => s.state.arms ?? EMPTY_ARRAY)
  const [open, setOpen] = useState<string | null>(null)

  if (!arms.length) {
    return (
      <Panel title="Stratégies">
        <p className="px-1 py-3 text-[11px] leading-snug text-dim">
          Aucune stratégie publiée. Le bot tourne-t-il&nbsp;?
        </p>
      </Panel>
    )
  }

  const totalPnl = arms.reduce((sum, a) => sum + (a.stats?.total_pnl_usd ?? 0), 0)
  const totalTrades = arms.reduce((sum, a) => sum + (a.stats?.total_trades ?? 0), 0)
  const active = arms.filter((a) => (a.stats?.open_positions ?? 0) > 0).length

  return (
    <Panel title={`Stratégies · ${arms.length}`}>
      <div className="mb-2 flex items-baseline gap-3 px-1 font-mono text-[10px] text-dim">
        <span>
          {totalTrades} trades cumulés
        </span>
        <span className={pnlColor(totalPnl)}>{usd(totalPnl)}</span>
        <span className="flex-1" />
        <span>{active} en position</span>
      </div>

      {/* Chaque bras est dépliable : la vue reste lisible à 7 stratégies,
          et le détail des règles arrive à la demande. */}
      <div>
        {arms.map((arm) => (
          <ArmRow
            key={arm.name}
            arm={arm}
            open={open === arm.name}
            onToggle={() => setOpen(open === arm.name ? null : arm.name)}
          />
        ))}
      </div>

      <p className="mt-2 px-1 text-[9px] leading-snug text-dim">
        barre&nbsp;: vus → retenus → au-dessus du seuil. La chute est
        l'information&nbsp;— un bras qui ne qualifie rien a un filtre à revoir.
      </p>

      <h3 className="mb-1 mt-4 text-[10px] uppercase tracking-wide text-dim">Confluence</h3>
      <Confluence />
    </Panel>
  )
}
