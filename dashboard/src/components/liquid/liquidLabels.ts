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

/** Ligne « au marché » d'un compte : null tant que le runner n'a rien valorisé. */
export function marketLine(compte: {
  equite_marche?: number; pnl_latent?: number; methodes_valorisation?: Record<string, number>
} | undefined): string | null {
  if (!compte || compte.equite_marche == null || compte.pnl_latent == null) return null
  const methods = compte.methodes_valorisation ?? {}
  const approx = (methods.milieu ?? 0) + (methods.cout ?? 0) + (methods.carnet_partiel ?? 0)
  const note = approx > 0 ? ` · ${approx} estimé${approx > 1 ? 's' : ''} hors carnet` : ''
  return `au marché ${compte.equite_marche.toFixed(2)} $ · latent ${signed(compte.pnl_latent)}${note}`
}

/** Baisse depuis le plus haut, lisible (jamais au-delà de 100 %). */
export function drawdownLabel(dd: { drawdown_pic_pct: number | null } | undefined): string {
  return dd?.drawdown_pic_pct == null ? '—' : `${dd.drawdown_pic_pct.toFixed(1)} %`
}

/** Engagé / limite (total, et paris de plus de 24 h). Null si pas de limite. */
export function engagementLine(e: {
  engage: number; limite?: number; engage_long?: number; limite_long?: number; long_heures?: number
} | null | undefined): string | null {
  if (!e) return null
  const parts: string[] = []
  if (e.limite != null) parts.push(`engagé ${e.engage.toFixed(2)} / ${e.limite.toFixed(0)} $`)
  if (e.limite_long != null && e.engage_long != null)
    parts.push(`> ${e.long_heures ?? 24} h ${e.engage_long.toFixed(2)} / ${e.limite_long.toFixed(0)} $`)
  return parts.length ? parts.join(' · ') : null
}

/** Le bras est-il bloqué par une limite d'engagement ? */
export function engagementBlocked(e: { depasse?: boolean; disponible?: number } | null | undefined): boolean {
  return !!e && (e.depasse === true || (e.disponible != null && e.disponible < 10))
}
