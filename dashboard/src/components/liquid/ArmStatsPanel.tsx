import { Panel, Stat, Empty } from '../Panel'
import { pnlColor, prob } from '../../lib/format'
import type { PredictionArm, PredictionSummary } from '../../lib/predictionTypes'
import { DOMAIN_LABEL, ratio, signed } from './liquidLabels'

function Calibration({ arm }: { arm: PredictionArm }) {
  const rows = arm.stats.calibration.filter((r) => r.paris > 0)
  if (rows.length === 0) return null
  return (
    <div className="mt-4 overflow-x-auto px-1">
      <div className="mb-1.5 text-[11px] uppercase tracking-wider text-dim">
        Par tranche de prix payé — bras {arm.arm}
      </div>
      <table className="w-full min-w-[420px] whitespace-nowrap text-left text-[11px]">
        <thead className="text-[11px] uppercase tracking-wider text-dim">
          <tr>
            <th className="px-2 py-1 font-medium">Tranche</th>
            <th className="px-2 py-1 text-right font-medium">Paris</th>
            <th className="px-2 py-1 text-right font-medium">Prix moyen payé</th>
            <th className="px-2 py-1 text-right font-medium">Part gagnée</th>
            <th className="px-2 py-1 text-right font-medium">P&L net</th>
          </tr>
        </thead>
        <tbody className="font-mono tabular-nums">
          {rows.map((r) => (
            <tr key={r.tranche} className="border-t border-edge/40">
              <td className="px-2 py-1.5 text-ink">{r.tranche}</td>
              <td className="px-2 py-1.5 text-right text-muted">{r.paris}</td>
              <td className="px-2 py-1.5 text-right text-muted">{prob(r.prix_moyen)}</td>
              <td className="px-2 py-1.5 text-right text-ink">{prob(r.part_gagnee)}</td>
              <td className={`px-2 py-1.5 text-right ${pnlColor(r.pnl_net)}`}>{signed(r.pnl_net)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-1.5 text-[11px] leading-snug text-dim">
        Si la part gagnée est au-dessus du prix moyen payé, la tranche était sous-cotée. Sous 10 paris par
        tranche, c’est du bruit.
      </p>
    </div>
  )
}

/** Chiffres du bras suivi : tout ce qui est affiché parle de CE bras. */
export function ArmStatsPanel({ arm, armName, cfg }: {
  arm: PredictionArm | null; armName: string | null; cfg: PredictionSummary['config']
}) {
  const s = arm?.stats
  const byDomain = Object.entries(s?.par_domaine ?? {})
  return (
    <Panel title={`Chiffres du bras ${armName ?? '—'}`}
            figure={s && s.paris_regles ? signed(s.pnl_net) : '—'}
            figureTone={s && s.paris_regles ? (s.pnl_net >= 0 ? 'gain' : 'loss') : 'neutral'}
            hint={arm?.description}>
            {s && arm && s.paris_regles > 0 ? (
              <>
                <div className="grid grid-cols-2 gap-4 px-1 sm:grid-cols-3">
                  <Stat label="Paris réglés" value={String(s.paris_regles)}
                    hint={`seuil de verdict : ${cfg.min_paris_verdict}`}
                    tone={s.paris_regles < cfg.min_paris_verdict ? 'warn' : 'neutral'} />
                  <Stat label="Gagnés" value={s.win_rate == null ? '—' : `${(s.win_rate * 100).toFixed(1)} %`} />
                  <Stat label="Profit factor" value={ratio(s.profit_factor)} hint="gains ÷ pertes, frais déduits" />
                  <Stat label="Drawdown max" value={s.drawdown_max_pct == null ? '—' : `${s.drawdown_max_pct.toFixed(1)} %`}
                    hint="du capital fictif" tone={(s.drawdown_max_pct ?? 0) > 10 ? 'loss' : 'neutral'} />
                  <Stat label="Pires 5 %" value={s.pires_5pct_moyenne == null ? '—' : signed(s.pires_5pct_moyenne)}
                    hint="moyenne par pari" />
                  <Stat label="Brier bras / marché"
                    value={`${ratio(s.brier_bras, 3)} / ${ratio(s.brier_marche, 3)}`}
                    hint="erreur de probabilité, plus bas = mieux" />
                </div>
                {byDomain.length > 1 && (
                  <div className="mt-4 grid grid-cols-2 gap-4 px-1 sm:grid-cols-3">
                    {byDomain.map(([domain, row]) => (
                      <Stat key={domain} label={DOMAIN_LABEL[domain] ?? domain}
                        value={signed(row.pnl_net)} tone={row.pnl_net >= 0 ? 'gain' : 'loss'}
                        hint={`${row.gagnes} gagnés sur ${row.paris} — bras ${arm.arm}`} />
                    ))}
                  </div>
                )}
                <Calibration arm={arm} />
              </>
            ) : (
              <Empty>
                {arm?.role === 'observateur'
                  ? 'Ce bras observe les incohérences entre marchés, il ne parie pas.'
                  : 'Aucun pari réglé pour ce bras. Pas de chiffre plutôt qu’un zéro.'}
              </Empty>
            )}
            {s && s.paris_indetermines > 0 && (
              <p className="mt-3 px-1 text-[11px] text-warn">
                {s.paris_indetermines} pari(s) indéterminé(s) : issue non établie, exclus de tous les chiffres.
              </p>
            )}
          </Panel>
  )
}
