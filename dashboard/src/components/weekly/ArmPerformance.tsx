import { Fragment, useState } from 'react'
import { Panel, Empty } from '../Panel'
import { usd, pnlColor } from '../../lib/format'
import { useArmPerformance } from '../../lib/useArmPerformance'
import type { ArmPerformance as ArmPerf, PerformanceWindow } from '../../lib/types'

const WINDOWS: { key: PerformanceWindow; label: string }[] = [
  { key: '7d', label: '7 j' },
  { key: '30d', label: '30 j' },
  { key: 'all', label: 'tout' },
]

function formatPf(pf: number | null, grossWin: number): string {
  if (pf === null) return grossWin > 0 ? '∞' : '—'
  return pf.toFixed(2)
}

/** WR avec son intervalle : « 33 % » seul sur 6 trades se lit comme une
 *  mesure, alors que l'intervalle va de presque nul à plutôt bon. */
function WinRate({ row }: { row: ArmPerf }) {
  if (!row.trades) return <>—</>
  const ic = row.win_rate_interval
  return (
    <span title={ic ? `IC95 ${ic.low.toFixed(0)}–${ic.high.toFixed(0)} %` : undefined}>
      {row.win_rate.toFixed(0)}%
      {ic && (
        <span className="text-[9px] text-dim/70"> [{ic.low.toFixed(0)}–{ic.high.toFixed(0)}]</span>
      )}
    </span>
  )
}

function DailyRows({ row }: { row: ArmPerf }) {
  if (!row.daily.length) return <p className="text-dim">Aucune clôture dans la fenêtre.</p>
  return (
    <div className="flex flex-wrap gap-1">
      {row.daily.map((day) => (
        <span
          key={day.date}
          className="rounded bg-white/5 px-1.5 py-0.5 tabular-nums"
          title={`${day.trades} trade(s)`}
        >
          <span className="text-dim">{day.date.slice(5)}</span>{' '}
          <span className={pnlColor(day.pnl_usd)}>{usd(day.pnl_usd, 2)}</span>
        </span>
      ))}
    </div>
  )
}

/**
 * P&L PAR BRAS sur 7 j / 30 j / tout : trades, WR (IC95), PF, P&L, drawdown
 * de la fenêtre, verdict contre le témoin, et P&L jour par jour en dépliant.
 *
 * Chaque ligne nomme son bras ; la ligne « flotte » est explicitement une
 * somme. Le drawdown n'y figure pas : il ne s'additionne pas entre bras.
 */
export function ArmPerformance() {
  const [periode, setPeriode] = useState<PerformanceWindow>('7d')
  const [open, setOpen] = useState<string | null>(null)
  const { data, error } = useArmPerformance(periode)

  const selector = (
    <div className="mb-2 flex gap-1" role="tablist" aria-label="Fenêtre de calcul">
      {WINDOWS.map((w) => (
        <button
          key={w.key}
          role="tab"
          aria-selected={periode === w.key}
          onClick={() => setPeriode(w.key)}
          className={`rounded px-2 py-0.5 font-mono text-[10px] ${
            periode === w.key ? 'bg-toxic/20 text-toxic' : 'bg-white/5 text-dim hover:text-ink'
          }`}
        >
          {w.label}
        </button>
      ))}
    </div>
  )

  if (!data) {
    return (
      <Panel title="P&L par bras">
        {selector}
        <Empty>{error ? `Erreur de chargement : ${error}` : 'Chargement…'}</Empty>
      </Panel>
    )
  }

  const rows = [...data.arms].sort((a, b) => b.pnl_usd - a.pnl_usd)

  return (
    <Panel
      title="P&L par bras"
      hint={`mise papier de ${usd(data.starting_capital_per_arm)} par bras · coûts de sortie inclus`}
    >
      {selector}
      <div className="overflow-x-auto">
        {/* Largeur minimale : sur téléphone le tableau défile dans le panneau
            au lieu d'écraser WR, PF et P&L les uns contre les autres. */}
        <table className="w-full min-w-[38rem] border-collapse font-mono text-[11px] [&_td+td]:pl-3 [&_td]:whitespace-nowrap [&_th+th]:pl-3 [&_th]:whitespace-nowrap">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wide text-dim">
              <th className="pb-1.5 font-medium">Bras</th>
              <th className="pb-1.5 text-right font-medium">P&amp;L</th>
              <th className="pb-1.5 text-right font-medium">Trades</th>
              <th className="pb-1.5 text-right font-medium">WR</th>
              <th className="pb-1.5 text-right font-medium">PF</th>
              <th className="pb-1.5 text-right font-medium">DD</th>
              <th className="pb-1.5 pl-2 font-medium">vs témoin</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <Fragment key={row.arm}>
                <tr
                  className="cursor-pointer border-t border-edge/40 hover:bg-white/[0.03]"
                  onClick={() => setOpen(open === row.arm ? null : row.arm)}
                  aria-expanded={open === row.arm}
                >
                  <td className="py-1.5 text-ink">{row.arm}</td>
                  <td className={`py-1.5 text-right tabular-nums ${row.trades ? pnlColor(row.pnl_usd) : 'text-dim'}`}>
                    {row.trades ? usd(row.pnl_usd, 2) : '—'}
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-dim">{row.trades || '—'}</td>
                  <td className="py-1.5 text-right tabular-nums text-dim">
                    <WinRate row={row} />
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-dim">
                    {row.trades ? formatPf(row.profit_factor, row.gross_win) : '—'}
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-blood/80">
                    {row.trades ? `-${row.max_drawdown_pct.toFixed(1)}%` : '—'}
                  </td>
                  <td className="py-1.5 pl-2 text-[10px] text-dim">
                    {row.verdict ? row.verdict.verdict : 'référence'}
                  </td>
                </tr>
                {open === row.arm && (
                  <tr>
                    <td colSpan={7} className="pb-2 pt-1 text-[10px]">
                      <DailyRows row={row} />
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            <tr className="border-t border-edge text-dim" title={data.fleet.label}>
              <td className="py-1.5">flotte (somme)</td>
              <td className={`py-1.5 text-right tabular-nums ${pnlColor(data.fleet.pnl_usd)}`}>
                {data.fleet.trades ? usd(data.fleet.pnl_usd, 2) : '—'}
              </td>
              <td className="py-1.5 text-right tabular-nums">{data.fleet.trades || '—'}</td>
              <td className="py-1.5 text-right tabular-nums">
                {data.fleet.trades ? `${data.fleet.win_rate.toFixed(0)}%` : '—'}
              </td>
              <td className="py-1.5 text-right tabular-nums">
                {data.fleet.trades && data.fleet.profit_factor !== null
                  ? data.fleet.profit_factor.toFixed(2)
                  : '—'}
              </td>
              <td className="py-1.5 text-right">—</td>
              <td className="py-1.5 pl-2 text-[10px]">non agrégeable</td>
            </tr>
          </tbody>
        </table>
      </div>
    </Panel>
  )
}
