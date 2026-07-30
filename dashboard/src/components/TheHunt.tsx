import { AnimatePresence, motion } from 'framer-motion'
import { useStore } from '../lib/store'
import { EMPTY_ARRAY } from '../lib/empty'
import { Panel, Empty } from './Panel'
import { price, compact, age, pct, pnlColor } from '../lib/format'
import type { Candidate } from '../lib/types'

/** Jauge du score : la porte d'entrée est le score ABSOLU, pas le classement. */
function ScoreBadge({ candidate, threshold }: { candidate: Candidate; threshold: number }) {
  const passes = candidate.alpha_score_absolute >= threshold
  return (
    <div className="text-right">
      <div
        className={`font-mono text-lg font-semibold leading-none tabular-nums ${
          passes ? 'text-toxic' : 'text-ink/70'
        }`}
        title={`Score absolu ${candidate.alpha_score_absolute} — décide l'entrée`}
      >
        {candidate.alpha_score_absolute.toFixed(0)}
      </div>
      <div
        className="mt-0.5 font-mono text-[10px] text-dim tabular-nums"
        title={`Score de classement ${candidate.alpha_score} — sert au tri`}
      >
        rang {candidate.alpha_score.toFixed(0)}
      </div>
    </div>
  )
}

function GemCard({ candidate, threshold }: { candidate: Candidate; threshold: number }) {
  const passes = candidate.alpha_score_absolute >= threshold
  const buyRatio =
    candidate.buys_5m + candidate.sells_5m > 0
      ? candidate.buys_5m / (candidate.buys_5m + candidate.sells_5m)
      : 0.5

  return (
    <motion.a
      href={candidate.url ?? undefined}
      target="_blank"
      rel="noreferrer"
      layout
      initial={{ opacity: 0, y: -8 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, height: 0, marginBottom: 0 }}
      transition={{ duration: 0.22 }}
      className={`group mb-2 block rounded-lg border px-3 py-2.5 transition-colors ${
        passes
          ? 'border-toxic/40 bg-toxic/[0.04] hover:border-toxic/70'
          : 'border-edge/70 bg-void/40 hover:border-edge'
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate font-semibold">{candidate.symbol}</span>
            {passes && (
              <span className="shrink-0 rounded bg-toxic/15 px-1.5 py-px text-[9px] font-bold uppercase tracking-wide text-toxic">
                qualifié
              </span>
            )}
          </div>
          <div className="mt-0.5 font-mono text-xs text-dim tabular-nums">
            {price(candidate.price_usd)}
            <span className={`ml-2 ${pnlColor(candidate.price_change_5m)}`}>
              {pct(candidate.price_change_5m)} 5m
            </span>
          </div>
        </div>
        <ScoreBadge candidate={candidate} threshold={threshold} />
      </div>

      <dl className="mt-2 grid grid-cols-4 gap-2 font-mono text-[11px] tabular-nums">
        <div>
          <dt className="text-[9px] uppercase tracking-wide text-dim">liq</dt>
          <dd>${compact(candidate.liquidity_usd)}</dd>
        </div>
        <div>
          <dt className="text-[9px] uppercase tracking-wide text-dim">vol 1h</dt>
          <dd>${compact(candidate.volume_1h)}</dd>
        </div>
        <div>
          <dt className="text-[9px] uppercase tracking-wide text-dim">âge</dt>
          <dd>{age(candidate.age_hours)}</dd>
        </div>
        <div>
          <dt className="text-[9px] uppercase tracking-wide text-dim">holders</dt>
          <dd title={candidate.holders_is_exact === false ? 'borne inférieure' : undefined}>
            {candidate.holders == null
              ? '—'
              : `${candidate.holders_is_exact === false ? '≥' : ''}${compact(candidate.holders)}`}
          </dd>
        </div>
      </dl>

      <div className="mt-2 flex items-center gap-3 text-[10px] text-dim">
        {candidate.rugcheck_score != null && (
          <span
            className={
              candidate.rugcheck_score >= 70 ? 'text-toxic/80' : 'text-blood'
            }
            title="Score de sûreté RugCheck (plus haut = plus sûr)"
          >
            sûreté {candidate.rugcheck_score.toFixed(0)}
          </span>
        )}
        {candidate.top_holder_pct != null && (
          <span title="Part du plus gros portefeuille">
            top {candidate.top_holder_pct.toFixed(1)}%
          </span>
        )}
        {candidate.social_mentions_1h != null && (
          <span title={`${candidate.social_unique_authors ?? 0} auteurs distincts`}>
            {candidate.social_mentions_1h} mentions
          </span>
        )}
        {/* Pression acheteuse sur 5 min */}
        <span className="ml-auto flex items-center gap-1" title={`${candidate.buys_5m} achats / ${candidate.sells_5m} ventes`}>
          <span className="h-1 w-10 overflow-hidden rounded-full bg-blood/40">
            <span
              className="block h-full bg-toxic"
              style={{ width: `${Math.round(buyRatio * 100)}%` }}
            />
          </span>
        </span>
      </div>
    </motion.a>
  )
}

export function TheHunt() {
  const candidates = useStore((s) => s.state.candidates ?? EMPTY_ARRAY)
  const rejected = useStore((s) => s.state.rejected ?? EMPTY_ARRAY)
  const scan = useStore((s) => s.state.scan)
  const threshold = useStore((s) => s.state.entry_threshold ?? 75)

  return (
    <Panel
      title="The Hunt"
      count={`${candidates.length} qualifiés · ${scan?.cached_skipped ?? 0} sautés`}
    >
      {candidates.length === 0 && rejected.length === 0 ? (
        <Empty>Aucun candidat ce cycle. Le bot scanne.</Empty>
      ) : (
        <>
          <AnimatePresence initial={false}>
            {candidates.map((candidate) => (
              <GemCard key={candidate.token_address} candidate={candidate} threshold={threshold} />
            ))}
          </AnimatePresence>

          {rejected.length > 0 && (
            <details className="mt-3 rounded-lg border border-edge/50 px-3 py-2">
              <summary className="cursor-pointer text-[11px] text-dim">
                {rejected.length} rejeté{rejected.length > 1 ? 's' : ''} ce cycle
              </summary>
              <ul className="mt-2 space-y-1">
                {rejected.map((candidate) => (
                  <li key={candidate.token_address} className="text-[11px] leading-snug">
                    <span className="font-medium text-ink/80">{candidate.symbol}</span>
                    <span className="text-dim"> — {candidate.rejected_reason}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </Panel>
  )
}
