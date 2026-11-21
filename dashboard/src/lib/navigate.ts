import { useStore } from './store'
import { LIQUID_TABS, MEME_TABS, SPACES } from './tabs'
import type { UrlState } from './urlState'

/** Applique une cible de navigation et l'annonce aux lecteurs d'écran. */
export function navigateTo(target: Partial<UrlState>, label: string): void {
  const st = useStore.getState()
  const moves =
    (target.view !== undefined && target.view !== st.view) ||
    (target.liquidView !== undefined && target.liquidView !== st.liquidView) ||
    (target.space !== undefined && target.space !== st.space)
  // Nouvelle vue : le panneau et les filtres repartent de zéro, sauf cible explicite.
  st.applyUrlState(moves ? { panel: null, filters: {}, ...target } : target)
  st.announce(`Navigation : ${label}`)
}

export function goToTabIndex(index: number): void {
  const st = useStore.getState()
  const tabs = st.space === 'liquid' ? LIQUID_TABS : MEME_TABS
  const tab = tabs[index]
  if (!tab) return
  if (st.space === 'liquid') navigateTo({ liquidView: tab.id as UrlState['liquidView'] }, `onglet ${tab.label}`)
  else navigateTo({ view: tab.id as UrlState['view'] }, `onglet ${tab.label}`)
}

export function toggleSpace(): void {
  const current = useStore.getState().space
  const next = current === 'liquid' ? 'memecoins' : 'liquid'
  const label = SPACES.find((s) => s.id === next)?.label ?? next
  navigateTo({ space: next }, `espace ${label}`)
}

/** Lien partageable de la vue courante (état de l'URL déjà synchronisé). */
export async function copyViewLink(): Promise<boolean> {
  const url = window.location.href
  try {
    await navigator.clipboard.writeText(url)
    useStore.getState().announce('Lien de cette vue copié')
    return true
  } catch {
    useStore.getState().announce('Copie impossible : copiez l’adresse depuis la barre du navigateur')
    return false
  }
}
