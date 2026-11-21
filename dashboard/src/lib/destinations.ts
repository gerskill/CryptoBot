import { LIQUID_TABS, MEME_TABS } from './tabs'
import type { UrlState } from './urlState'

/**
 * Destinations de la palette : de la NAVIGATION uniquement. Aucune action
 * (arrêt, ordre, commande) ne doit apparaître ici.
 */
export type Destination = {
  id: string
  label: string
  group: string
  hint?: string
  keywords?: readonly string[]
  /** Fragment d'état appliqué tel quel par le store. */
  target: Partial<UrlState>
}

const WINDOW_LABEL: [UrlState['window'], string][] = [['7d', '7 jours'], ['30d', '30 jours'], ['all', 'tout l’historique']]

export function buildDestinations(data: {
  arms: readonly string[]
  positions: readonly string[]
}): Destination[] {
  const out: Destination[] = [
    { id: 'space:memecoins', label: 'Espace Memecoins', group: 'Espaces', hint: 'Solana · bot Alpha Loop',
      keywords: ['bot', 'solana'], target: { space: 'memecoins' } },
    { id: 'space:liquid', label: 'Espace Liquid', group: 'Espaces', hint: 'marchés · prédictions',
      keywords: ['prediction'], target: { space: 'liquid' } },
  ]
  for (const { id, label } of MEME_TABS) {
    out.push({ id: `tab:${id}`, label, group: 'Onglets Memecoins', target: { space: 'memecoins', view: id } })
  }
  for (const { id, label } of LIQUID_TABS) {
    out.push({ id: `liquid:${id}`, label, group: 'Onglets Liquid', hint: 'Liquid', target: { space: 'liquid', liquidView: id } })
  }
  for (const [window, label] of WINDOW_LABEL) {
    out.push({
      id: `window:${window}`, label: `Performance par bras · ${label}`, group: 'Panneaux',
      keywords: ['fenetre', 'periode', 'weekly'], target: { space: 'memecoins', view: 'weekly', window },
    })
  }
  for (const arm of arms(data.arms)) {
    out.push({
      id: `arm:${arm}`, label: `Bras ${arm}`, group: 'Bras', hint: 'Dashboard · stratégies',
      keywords: ['strategie'], target: { space: 'memecoins', view: 'dashboard', panel: arm },
    })
  }
  for (const symbol of arms(data.positions)) {
    out.push({
      id: `position:${symbol}`, label: `Position ${symbol}`, group: 'Positions', hint: 'Live Prices',
      keywords: ['token'], target: { space: 'memecoins', view: 'live' },
    })
  }
  return out
}

/** Dédoublonne en gardant l'ordre (plusieurs bras peuvent tenir le même token). */
function arms(names: readonly string[]): string[] {
  return [...new Set(names)]
}
