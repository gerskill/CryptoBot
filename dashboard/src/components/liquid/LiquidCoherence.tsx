import { Panel, Empty } from '../Panel'
import { usePolled } from '../../lib/usePrediction'
import type { CoherenceRow } from '../../lib/predictionTypes'
import { utc, prob } from '../../lib/format'
import { DOMAIN_LABEL } from './liquidLabels'

const TYPE_LABEL: Record<CoherenceRow['type'], string> = {
  somme_sous_100: 'Issues exclusives sous 100 %',
  somme_sur_100: 'Issues exclusives au-dessus de 100 %',
  echelle_non_monotone: 'Seuil haut plus cher que seuil bas',
  ecart_entre_venues: 'Même marché, deux prix',
}

function detail(row: CoherenceRow): string {
  if (row.type === 'somme_sous_100' || row.type === 'somme_sur_100') {
    return `${row.libelle ?? 'question'} : ${row.issues} issues, somme ${prob(row.somme_milieux ?? null)}`
  }
  if (row.type === 'echelle_non_monotone') {
    return `${row.sous_jacent} : > ${row.seuil_bas} à ${prob(row.prix_bas ?? null)}, > ${row.seuil_haut} à ${prob(row.prix_haut ?? null)}`
  }
  return `${row.sous_jacent} > ${row.seuil} : ${prob(row.prix_bas ?? null)} sur ${row.venue_basse}, ${prob(row.prix_haut ?? null)} sur ${row.venue_haute}`
}

/** Journal du bras observateur `pm_coherence`. Ce ne sont pas des paris. */
export function LiquidCoherence({ demo }: { demo: boolean }) {
  const { data, error } = usePolled<{ total: number; rows: CoherenceRow[] }>(
    `/api/prediction/coherence?limit=300${demo ? '&demo=true' : ''}`, 30_000)
  if (!data) return <Empty>{error ? `Journal illisible (${error}).` : 'Chargement…'}</Empty>
  const rows = data.rows.slice().reverse()
  return (
    <Panel title="Incohérences relevées · bras pm_coherence" count={data.total}
      hint="Relevées sur les prix milieux, à chaque cycle. Une incohérence au prix milieu n’est pas un gain : il faudrait encore pouvoir acheter à ce prix. Ce journal sert à décider s’il vaut la peine de créer un bras qui les parie.">
      {rows.length === 0 ? <Empty>Aucune incohérence relevée pour l’instant.</Empty> : (
        <div className="max-h-[640px] overflow-auto">
          <table className="w-full min-w-[620px] text-left text-[11px]">
            <thead className="sticky top-0 bg-surface text-[10px] uppercase tracking-wider text-dim">
              <tr>
                <th className="px-2 py-1.5 font-medium">Relevé (UTC)</th>
                <th className="px-2 py-1.5 font-medium">Type</th>
                <th className="px-2 py-1.5 font-medium">Domaine</th>
                <th className="px-2 py-1.5 font-medium">Détail</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={`${row.t}-${index}`} className="border-t border-edge/40">
                  <td className="whitespace-nowrap px-2 py-1.5 font-mono text-muted">{utc(row.t)}</td>
                  <td className="px-2 py-1.5 text-ink">{TYPE_LABEL[row.type] ?? row.type}</td>
                  <td className="px-2 py-1.5 text-muted">{row.domain ? (DOMAIN_LABEL[row.domain] ?? row.domain) : '—'}</td>
                  <td className="px-2 py-1.5 font-mono text-muted">{detail(row)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  )
}
