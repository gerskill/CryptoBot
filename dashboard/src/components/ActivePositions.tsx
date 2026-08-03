import { AnimatePresence, motion } from 'framer-motion'
import { useStore } from '../lib/store'
import { EMPTY_ARRAY } from '../lib/empty'
import { Panel, Empty } from './Panel'
import { price, usd, pct, multiple, duration, compact, pnlColor } from '../lib/format'
import { usePulse } from '../lib/useLiveState'
import type { Position } from '../lib/types'

/**
 * Barre de progression entre le stop loss et TP1.
 * Le curseur montre où se situe le P&L courant : à gauche = vers la sortie
 * perdante, à droite = vers la première prise de profit.
 */
function RiskBar({ position }: { position: Position }) {
  const low = position.stop_loss_pct
  const high = position.take_profit_1
  const clamped = Math.max(low, Math.min(high, position.pnl_pct))
  const ratio = (clamped - low) / (high - low)
  const zero = (0 - low) / (high - low)

  return (
    <div className="mt-3">
      <div className="relative h-1.5 overflow-hidden rounded-full bg-void">
        <div className="absolute inset-y-0 left-0 bg-blood/25" style={{ width: `${zero * 100}%` }} />
        <div
          className="absolute inset-y-0 bg-toxic/20"
          style={{ left: `${zero * 100}%`, right: 0 }}
        />
        {/* Repère du point mort */}
        <div className="absolute inset-y-0 w-px bg-dim/60" style={{ left: `${zero * 100}%` }} />
        <motion.div
          layout
          className={`absolute top-1/2 h-3 w-3 -translate-y-1/2 rounded-full ring-2 ring-void ${
            position.pnl_pct >= 0 ? 'bg-toxic' : 'bg-blood'
          }`}
          style={{ left: `calc(${ratio * 100}% - 6px)` }}
        />
      </div>
      <div className="mt-1 flex justify-between font-mono text-[10px] text-dim tabular-nums">
        <span className={position.breakeven_moved ? 'text-toxic' : ''}>
          {position.breakeven_moved ? 'BE protégé' : `SL ${low}%`}
        </span>
        <span>TP1 +{high}%</span>
      </div>
    </div>
  )
}

// Au-delà de cette fraction de la distance entrée -> SL déjà parcourue, la
// position est en danger réel : bordure pulsante. Symétrique côté TP1 avec
// PROXIMITE_TP1 — « à moins de 30 % du TP1 » = 70 % de la distance couverte.
const DANGER_SL_RATIO = 0.5
const PROXIMITE_TP1_RATIO = 0.7
// Sous ce seuil de temps restant, le compte à rebours du time stop s'affiche.
// Au-dessus, c'est du bruit permanent — la barre suffit.
const TIME_STOP_ALERT_RATIO = 0.8

function PositionCard({ position }: { position: Position }) {
  const pulse = usePulse(position.current_price)
  const timeRatio = Math.min(1, position.duration_min / position.max_hold_time_minutes)
  const timeRemaining = Math.max(0, position.max_hold_time_minutes - position.duration_min)

  // `stop_loss_pct` est DÉJÀ `effective_stop_loss_pct` côté API (tampon de
  // glissement inclus) : l'alerte se déclenche sur le seuil RÉEL, pas sur la
  // règle nominale — sinon la bordure resterait calme alors que le stop est
  // sur le point de se déclencher pour de vrai.
  const enDanger = position.pnl_pct <= position.stop_loss_pct * DANGER_SL_RATIO
  const presDuTp1 =
    !enDanger && position.pnl_pct >= position.take_profit_1 * PROXIMITE_TP1_RATIO
  const pulseClass = enDanger ? 'pulse-danger' : presDuTp1 ? 'pulse-target' : ''

  return (
    <motion.article
      layout
      initial={{ opacity: 0, scale: 0.96 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.9, transition: { duration: 0.35 } }}
      transition={{ type: 'spring', stiffness: 300, damping: 26 }}
      className={`mb-3 rounded-xl border px-4 py-3 ${pulseClass} ${
        position.pnl_pct >= 0
          ? 'border-toxic/35 bg-toxic/[0.03]'
          : 'border-blood/35 bg-blood/[0.03]'
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-lg font-semibold">{position.symbol}</span>
            {/* Indispensable : deux stratégies peuvent tenir le MÊME token
                avec des stop loss opposés. Sans le bras, deux cartes
                identiques afficheraient des règles contradictoires. */}
            {position.arm && (
              <span className="rounded bg-white/10 px-1.5 py-px font-mono text-[9px] text-muted">
                {position.arm}
              </span>
            )}
            {position.trailing_active && (
              <span className="rounded bg-gem/15 px-1.5 py-px text-[9px] font-bold uppercase text-gem">
                trailing
              </span>
            )}
            {position.tp1_hit && (
              <span className="rounded bg-toxic/15 px-1.5 py-px text-[9px] font-bold uppercase text-toxic">
                TP1 pris
              </span>
            )}
          </div>
          <div className="mt-0.5 font-mono text-[11px] text-dim tabular-nums">
            entrée {price(position.entry_price)} → <span className={pulse}>{price(position.current_price ?? 0)}</span>
          </div>
        </div>

        <div className="text-right">
          <div className={`font-mono text-3xl font-bold leading-none tabular-nums ${pnlColor(position.pnl_pct)}`}>
            {multiple(position.pnl_pct)}
          </div>
          <div className={`mt-1 font-mono text-xs tabular-nums ${pnlColor(position.pnl_usd)}`}>
            {pct(position.pnl_pct)} · {position.pnl_usd >= 0 ? '+' : ''}{usd(position.pnl_usd, 2)}
          </div>
        </div>
      </div>

      <RiskBar position={position} />

      {/* Pic et creux depuis l'entrée : la donnée existe déjà côté monitoring
          (`high_water_pct`/`low_water_pct`), elle manquait juste à la carte.
          C'est ce qui dit si un P&L calme cache une trajectoire agitée. */}
      <div className="mt-2 flex items-center gap-3 font-mono text-[10px] tabular-nums text-dim">
        <span>
          pic <span className="text-toxic">{pct(position.high_water_pct)}</span>
        </span>
        <span>
          creux <span className="text-blood">{pct(position.low_water_pct)}</span>
        </span>
      </div>

      <div className="mt-2 flex items-center justify-between font-mono text-[10px] text-dim tabular-nums">
        <span>mise {usd(position.size_usd, 2)}</span>
        <span>reste {Math.round(position.remaining_fraction * 100)}%</span>
        <span>liq ${compact(position.liquidity_at_entry)}</span>
        <span title={`Time stop à ${position.max_hold_time_minutes} min`}>
          {timeRatio > TIME_STOP_ALERT_RATIO
            ? `time stop dans ${Math.round(timeRemaining)} min`
            : duration(position.duration_min)}
          <span className="ml-1 inline-block h-1 w-8 overflow-hidden rounded-full bg-void align-middle">
            <span
              className={`block h-full ${timeRatio > TIME_STOP_ALERT_RATIO ? 'bg-warn' : 'bg-edge'}`}
              style={{ width: `${timeRatio * 100}%` }}
            />
          </span>
        </span>
      </div>
    </motion.article>
  )
}

export function ActivePositions() {
  const positions = useStore((s) => s.state.positions ?? EMPTY_ARRAY)
  const stats = useStore((s) => s.state.stats)
  const aggregate = useStore((s) => s.state.aggregate)

  // Le compteur porte sur TOUTES les stratégies. Il n'affichait que celles du
  // témoin : deux bras détenaient une position chacun et le panneau annonçait
  // « 1 ». Un tableau de bord qui sous-estime l'exposition est pire qu'absent.
  const exposure = aggregate?.exposure_usd ?? 0
  const armsTrading = aggregate?.arms_trading ?? 0

  return (
    <Panel
      title="Positions actives"
      count={`${positions.length}`}
      figure={exposure > 0 ? usd(exposure) : undefined}
      hint={
        positions.length > 0
          ? `exposé sur ${armsTrading} stratégie${armsTrading > 1 ? 's' : ''}`
          : undefined
      }
    >
      {stats && stats.cooldown_min > 0 && (
        <div className="mb-3 rounded-lg border border-blood/40 bg-blood/10 px-3 py-2 text-xs text-blood">
          Cooldown actif — {Math.round(stats.cooldown_min)} min restantes après
          {' '}{stats.consecutive_losses} pertes consécutives. Aucune nouvelle entrée.
        </div>
      )}

      {positions.length === 0 ? (
        <Empty>
          Aucune position ouverte.
          <br />
          Le bot n'entre que si le score absolu franchit le seuil ET que la structure de prix valide.
        </Empty>
      ) : (
        <AnimatePresence initial={false}>
          {positions.map((position) => (
            <PositionCard key={position.id} position={position} />
          ))}
        </AnimatePresence>
      )}
    </Panel>
  )
}
