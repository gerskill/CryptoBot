import type { LiquidView, Space, View } from './store'

/** Onglets de chaque espace, dans l'ordre d'affichage (la source unique). */
export const SPACES: { id: Space; label: string; sub: string }[] = [
  { id: 'memecoins', label: 'Memecoins', sub: 'Solana · bot Alpha Loop' },
  { id: 'liquid', label: 'Liquid', sub: 'marchés · prédictions' },
]

export const MEME_TABS: { id: View; label: string }[] = [
  { id: 'compte', label: 'Compte' },
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'live', label: 'Live Prices' },
  { id: 'weekly', label: 'Weekly' },
  { id: 'config', label: 'Config' },
  { id: 'backtester', label: 'Backtester' },
  { id: 'pilotage', label: 'Pilotage' },
]

export const LIQUID_TABS: { id: LiquidView; label: string }[] = [
  { id: 'apercu', label: 'Aperçu et bras' },
  { id: 'predictions', label: 'Prédictions' },
  { id: 'marches', label: 'Marchés' },
  { id: 'paris', label: 'Paris et graphique' },
  { id: 'incoherences', label: 'Incohérences' },
]
