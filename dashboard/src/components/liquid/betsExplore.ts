import { useCallback } from 'react'
import { useFilter } from '../../lib/useFilter'
import { useExplore } from '../../lib/useExplore'
import type { PredictionBet } from '../../lib/predictionTypes'
import { BET_ACCESSORS, BET_DESC_FIRST, issueLabel } from './betSort'
import { DOMAIN_LABEL } from './liquidLabels'

const SEARCH_OF = (b: PredictionBet) =>
  [b.title, b.side_name, DOMAIN_LABEL[b.domain] ?? b.domain, b.venue, b.coin, issueLabel(b)]

/** Recherche, tri et filtres statut/domaine des paris d'un bras. */
export function useBetsExplore(armBets: PredictionBet[]) {
  // Statut et domaine vivent dans l'URL (lien partageable) ; le domaine est
  // validé contre ceux du bras affiché.
  const [statut, setStatut] = useFilter('statut', 'tous')
  const [domaine, setDomaine] = useFilter('domaine', 'tous')
  const domains = [...new Set(armBets.map((b) => b.domain))]
  const domaineActif = domains.includes(domaine) ? domaine : 'tous'
  const predicate = useCallback(
    (b: PredictionBet) =>
      (statut === 'tous' || b.status === statut) && (domaineActif === 'tous' || b.domain === domaineActif),
    [statut, domaineActif],
  )
  const explore = useExplore({
    rows: armBets,
    searchOf: SEARCH_OF,
    accessors: BET_ACCESSORS,
    predicate,
    filterActive: statut !== 'tous' || domaineActif !== 'tous',
    onReset: () => { setStatut('tous'); setDomaine('tous') },
    descFirst: BET_DESC_FIRST,
  })
  return { explore, statut, setStatut, domaine: domaineActif, setDomaine, domains }
}

export type BetsExplore = ReturnType<typeof useBetsExplore>
