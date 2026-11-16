import { usd } from '../../lib/format'

/** Libellés partagés de l'espace Liquid. Miroir de `src/prediction/markets.py::FAMILLES`. */

export const DOMAIN_LABEL: Record<string, string> = {
  crypto_seuil: 'Crypto · au-dessus d’un seuil',
  actions_seuil: 'Actions et indices · seuil',
  matieres_seuil: 'Matières premières · seuil',
  barriere: 'Barrière « touche »',
  tranche_prix: 'Tranches de prix',
  sport_match: 'Match',
  sport_tournoi: 'Vainqueur de tournoi',
  evenement: 'Question d’actualité',
  autre: 'Non classé',
}

export const FAMILLES: { id: string; label: string; what: string }[] = [
  { id: 'transverse', label: 'Transverse', what: 'Référence, surveillance et consensus, toutes catégories.' },
  { id: 'prix', label: 'Prix', what: 'Seuils et barrières sur crypto, actions, indices, matières premières.' },
  { id: 'sport', label: 'Sport', what: 'Matchs de football et de basket.' },
  { id: 'questions', label: 'Questions', what: 'Décisions de taux, introductions en bourse.' },
  { id: 'pourcentages', label: 'Pourcentages', what: 'Même règle, tranche de probabilité différente.' },
]

export const MARKET_FAMILLES = ['prix', 'sport', 'questions', 'autre'] as const

export const ROLE_LABEL: Record<string, string> = {
  temoin: 'témoin',
  regle: 'règle fixe',
  parieur: 'estimateur',
  consensus: 'consensus',
  observateur: 'observateur',
}

export const signed = (value: number, digits = 2) => `${value >= 0 ? '+' : '−'}${usd(Math.abs(value), digits)}`
export const ratio = (value: number | null | undefined, digits = 2) => (value == null ? '—' : value.toFixed(digits))
