import { pnlColor } from '../../lib/format'
import type { PredictionArm } from '../../lib/predictionTypes'
import { ROLE_LABEL, ratio, signed } from './liquidLabels'

export function ArmTable({ arms, selected, onSelect }: {
  arms: PredictionArm[]; selected: string | null; onSelect: (arm: string) => void
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[620px] whitespace-nowrap text-left text-[11px]">
        <thead className="text-[11px] uppercase tracking-wider text-dim">
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
                className={`h-11 cursor-pointer border-t border-edge/40 transition-colors hover:bg-raised ${active ? 'bg-raised' : ''} ${row.enabled ? '' : 'opacity-50'}`}>
                <td className="px-2 py-0 text-ink">
                  {/* Vrai bouton : sélectionnable au clavier, `aria-current` marque le bras suivi. */}
                  <button type="button" aria-current={active ? 'true' : undefined}
                    className="flex min-h-11 items-center">
                    <span className={`mr-1.5 inline-block h-1.5 w-1.5 rounded-full ${active ? 'bg-gem' : 'bg-edge-strong'}`} />
                    {row.arm}
                  </button>
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
