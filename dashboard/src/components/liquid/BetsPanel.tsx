import { Panel } from '../Panel'
import { ExploreBar } from '../explore/ExploreBar'
import type { OpenEvent } from '../explore/useDrawer'
import { BetTable } from './BetTable'
import type { BetsExplore } from './betsExplore'
import { DOMAIN_LABEL } from './liquidLabels'

const SELECT_CLASS = 'min-h-11 rounded-md bg-void px-2 font-mono text-xs text-ink ring-1 ring-edge'

/** Paris d'un bras : recherche, filtres statut/domaine, tri, pagination. */
export function BetsPanel({ model, armName, selectedId, onOpen }: {
  model: BetsExplore
  armName: string | null
  selectedId: string | null
  onOpen: (id: string) => (event: OpenEvent) => void
}) {
  const { explore, statut, setStatut, domaine, setDomaine, domains } = model
  const label = `Paris du bras ${armName ?? '—'}`
  return (
    <Panel title={label} count={explore.visible.length}
      hint="Clique une ligne : son détail s’ouvre, et son entrée et son règlement se posent sur le graphique, à la même date et au même prix.">
      <ExploreBar label="Chercher un pari" placeholder="Marché, côté, domaine"
        query={explore.query} onQuery={explore.setQuery} shown={explore.visible.length}
        total={explore.total} isFiltered={explore.isFiltered} onReset={explore.reset}>
        <select aria-label="Statut" value={statut} onChange={(e) => setStatut(e.target.value)} className={SELECT_CLASS}>
          <option value="tous">tous les statuts</option><option value="ouvert">ouverts</option>
          <option value="regle">réglés</option><option value="indetermine">indéterminés</option>
        </select>
        <select aria-label="Domaine" value={domaine} onChange={(e) => setDomaine(e.target.value)} className={SELECT_CLASS}>
          <option value="tous">tous les domaines</option>
          {domains.map((d) => <option key={d} value={d}>{DOMAIN_LABEL[d] ?? d}</option>)}
        </select>
      </ExploreBar>
      <BetTable bets={explore.visible} total={explore.total} query={explore.query} label={label}
        selectedId={selectedId} ariaSort={explore.ariaSort} onSort={explore.toggleSort} onOpen={onOpen} />
    </Panel>
  )
}
