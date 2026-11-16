import { useState } from 'react'
import { Panel, Stat, Empty } from '../Panel'
import { pnlColor, utc, prob } from '../../lib/format'
import { useBetPrices } from '../../lib/usePrediction'
import type { PredictionArm, PredictionBet, PredictionSummary } from '../../lib/predictionTypes'
import { BetChart } from './BetChart'
import { DOMAIN_LABEL, ROLE_LABEL, ratio, signed } from './liquidLabels'

function ArmTable({ arms, selected, onSelect }: {
  arms: PredictionArm[]; selected: string | null; onSelect: (arm: string) => void
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[620px] whitespace-nowrap text-left text-[11px]">
        <thead className="text-[10px] uppercase tracking-wider text-dim">
          <tr>
            <th className="px-2 py-1.5 font-medium">Bras</th>
            <th className="px-2 py-1.5 font-medium">Rôle</th>
            <th className="px-2 py-1.5 text-right font-medium">Réglés</th>
            <th className="px-2 py-1.5 text-right font-medium">Ouverts</th>
            <th className="px-2 py-1.5 text-right font-medium">Gagnés</th>
            <th className="px-2 py-1.5 text-right font-medium">PF</th>
            <th className="px-2 py-1.5 text-right font-medium">P&L net</th>
            <th className="px-2 py-1.5 font-medium">Verdict</th>
          </tr>
        </thead>
        <tbody className="font-mono tabular-nums">
          {arms.map((row) => {
            const s = row.stats
            const active = row.arm === selected
            return (
              <tr key={row.arm} onClick={() => onSelect(row.arm)}
                className={`cursor-pointer border-t border-edge/40 transition-colors hover:bg-raised ${active ? 'bg-raised' : ''} ${row.enabled ? '' : 'opacity-50'}`}>
                <td className="px-2 py-2 text-ink">
                  <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${active ? 'bg-gem' : 'bg-edge-strong'}`} />
                  {row.arm}
                </td>
                <td className="px-2 py-2 text-muted">{row.enabled ? (ROLE_LABEL[row.role] ?? row.role) : 'désactivé'}</td>
                <td className="px-2 py-2 text-right text-ink">{s.paris_regles}</td>
                <td className="px-2 py-2 text-right text-muted">{row.ouverts}</td>
                <td className="px-2 py-2 text-right text-muted">
                  {s.win_rate == null ? '—' : `${(s.win_rate * 100).toFixed(0)} %`}
                </td>
                <td className="px-2 py-2 text-right text-muted">{ratio(s.profit_factor)}</td>
                <td className={`px-2 py-2 text-right ${s.paris_regles ? pnlColor(s.pnl_net) : 'text-dim'}`}>
                  {s.paris_regles ? signed(s.pnl_net) : '—'}
                </td>
                <td className="whitespace-normal px-2 py-2 font-sans text-muted">{row.role === 'observateur' ? 'ne parie pas' : s.verdict}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function BetTable({ bets, selectedId, onSelect }: {
  bets: PredictionBet[]; selectedId: string | null; onSelect: (id: string) => void
}) {
  if (bets.length === 0) return <Empty>Aucun pari pour ce bras.</Empty>
  return (
    <div className="max-h-[420px] overflow-auto">
      <table className="w-full min-w-[640px] whitespace-nowrap text-left text-[11px]">
        <thead className="sticky top-0 bg-surface text-[10px] uppercase tracking-wider text-dim">
          <tr>
            <th className="px-2 py-1.5 font-medium">Entrée (UTC)</th>
            <th className="px-2 py-1.5 font-medium">Marché</th>
            <th className="px-2 py-1.5 font-medium">Côté</th>
            <th className="px-2 py-1.5 text-right font-medium">Prix payé</th>
            <th className="px-2 py-1.5 text-right font-medium">Estimé</th>
            <th className="px-2 py-1.5 text-right font-medium">Issue</th>
            <th className="px-2 py-1.5 text-right font-medium">P&L net</th>
          </tr>
        </thead>
        <tbody className="font-mono tabular-nums">
          {bets.map((bet) => {
            const active = bet.id === selectedId
            return (
              <tr key={bet.id} onClick={() => onSelect(bet.id)}
                className={`cursor-pointer border-t border-edge/40 transition-colors hover:bg-raised ${active ? 'bg-raised' : ''}`}>
                <td className="whitespace-nowrap px-2 py-2 text-muted">{utc(bet.entry_time)}</td>
                <td className="whitespace-normal px-2 py-2 font-sans text-ink">{bet.title}</td>
                <td className="px-2 py-2 text-muted">{bet.side_name}</td>
                <td className="px-2 py-2 text-right text-ink">{prob(bet.entry_price)}</td>
                <td className="px-2 py-2 text-right text-muted">{prob(bet.prob_estimee)}</td>
                <td className="px-2 py-2 text-right text-muted">
                  {bet.status === 'regle' ? (bet.won ? 'gagné' : 'perdu') : bet.status === 'ouvert' ? 'ouvert' : 'indéterminé'}
                </td>
                <td className={`px-2 py-2 text-right ${bet.pnl_net == null ? 'text-dim' : pnlColor(bet.pnl_net)}`}>
                  {bet.pnl_net == null ? '—' : signed(bet.pnl_net)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}


function Calibration({ arm }: { arm: PredictionArm }) {
  const rows = arm.stats.calibration.filter((r) => r.paris > 0)
  if (rows.length === 0) return null
  return (
    <div className="mt-4 overflow-x-auto px-1">
      <div className="mb-1.5 text-[10px] uppercase tracking-wider text-dim">
        Par tranche de prix payé — bras {arm.arm}
      </div>
      <table className="w-full min-w-[420px] whitespace-nowrap text-left text-[11px]">
        <thead className="text-[10px] uppercase tracking-wider text-dim">
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
      <p className="mt-1.5 text-[10px] leading-snug text-dim">
        Si la part gagnée est au-dessus du prix moyen payé, la tranche était sous-cotée. Sous 10 paris par
        tranche, c’est du bruit.
      </p>
    </div>
  )
}

export function LiquidBets({ summary, bets, demo }: {
  summary: PredictionSummary; bets: PredictionBet[]; demo: boolean
}) {
  const [armChoice, setArmChoice] = useState<string | null>(null)
  const [betChoice, setBetChoice] = useState<string | null>(null)

  // Bras affiché : celui choisi, sinon le premier qui a des paris, sinon le
  // premier de la liste. Tous les chiffres de la page parlent de CE bras.
  const arms = summary.arms
  const fallbackArm = arms.find((a) => a.stats.paris_regles + a.ouverts > 0)?.arm ?? arms[0]?.arm ?? null
  const armName = arms.some((a) => a.arm === armChoice) ? armChoice : fallbackArm
  const arm = arms.find((a) => a.arm === armName) ?? null

  // Plus récents en premier : c'est ce qu'on vient regarder.
  const [statut, setStatut] = useState('tous')
  const [domaine, setDomaine] = useState('tous')
  const allArmBets = bets.filter((b) => b.arm === armName).slice().reverse()
  const armDomains = [...new Set(allArmBets.map((b) => b.domain))]
  const armBets = allArmBets.filter((b) =>
    (statut === 'tous' || b.status === statut) && (domaine === 'tous' || b.domain === domaine))
  const bet = armBets.find((b) => b.id === betChoice) ?? armBets[0] ?? null
  const prices = useBetPrices(bet?.coin ?? null, demo)

  const s = arm?.stats
  const cfg = summary.config
  const byDomain = Object.entries(s?.par_domaine ?? {})

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)]">
      <div className="grid min-w-0 grid-cols-1 gap-4">
        <Panel title="Bras de prédiction" count={arms.length}
          hint="Clique un bras : les chiffres, les paris et le graphique de la page le suivent.">
          <ArmTable arms={arms} selected={armName} onSelect={(name) => { setArmChoice(name); setBetChoice(null) }} />
        </Panel>

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
      </div>

      <div className="grid min-w-0 grid-cols-1 content-start gap-4">
        <Panel title={bet ? `Graphique du pari · ${bet.arm}` : 'Graphique du pari'}
          hint={bet ? `${bet.title} — côté ${bet.side_name} — pièce ${bet.coin}` : undefined}>
          {bet ? (
            <>
              <BetChart bet={bet} points={prices.points} />
              {prices.error && <p className="mt-2 text-[11px] text-blood">Prix illisibles : {prices.error}</p>}
            </>
          ) : (
            <Empty>Clique un pari dans le tableau pour le voir sur son graphique.</Empty>
          )}
        </Panel>

        <Panel title={`Paris du bras ${armName ?? '—'}`} count={armBets.length}
          hint="Clique une ligne : son entrée et son règlement se posent sur le graphique, à la même date et au même prix.">
          <div className="mb-2 flex flex-wrap gap-2 px-1 text-[10px] uppercase tracking-wider text-dim">
            <label className="flex items-center gap-1.5">Statut
              <select value={statut} onChange={(e) => setStatut(e.target.value)}
                className="rounded bg-void px-1.5 py-1 font-mono text-[10px] normal-case text-ink ring-1 ring-edge/60">
                <option value="tous">tous</option><option value="ouvert">ouverts</option>
                <option value="regle">réglés</option><option value="indetermine">indéterminés</option>
              </select>
            </label>
            <label className="flex items-center gap-1.5">Domaine
              <select value={armDomains.includes(domaine) ? domaine : 'tous'} onChange={(e) => setDomaine(e.target.value)}
                className="rounded bg-void px-1.5 py-1 font-mono text-[10px] normal-case text-ink ring-1 ring-edge/60">
                <option value="tous">tous</option>
                {armDomains.map((d) => <option key={d} value={d}>{DOMAIN_LABEL[d] ?? d}</option>)}
              </select>
            </label>
          </div>
          <BetTable bets={armBets} selectedId={bet?.id ?? null} onSelect={setBetChoice} />
        </Panel>
      </div>
    </div>
  )
}
