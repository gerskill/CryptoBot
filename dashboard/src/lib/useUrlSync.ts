import { useLayoutEffect } from 'react'
import { useStore } from './store'
import { DEFAULT_URL_STATE, historyAction, parseUrlState, serializeUrlState } from './urlState'
import type { UrlState } from './urlState'

type NavSlice = ReturnType<typeof useStore.getState>

export function selectUrlState(s: Pick<NavSlice, 'space' | 'view' | 'liquidView' | 'window' | 'panel' | 'filters'>): UrlState {
  return { space: s.space, view: s.view, liquidView: s.liquidView, window: s.window, panel: s.panel, filters: s.filters }
}

/** URL -> store, avec retour aux défauts pour tout paramètre absent. */
export function readUrlIntoStore(search: string): void {
  useStore.getState().applyUrlState({ ...DEFAULT_URL_STATE, ...parseUrlState(search) })
}

/** Safari lève SecurityError au-delà d'une centaine d'appels par 30 s : repli silencieux. */
function writeHistory(mode: 'push' | 'replace', url: string): void {
  try {
    window.history[mode === 'push' ? 'pushState' : 'replaceState'](null, '', url)
  } catch {
    // L'URL n'est qu'un confort de partage : l'état du store reste juste.
  }
}

const SEARCH_DEBOUNCE_MS = 300

function urlFor(state: UrlState): string {
  return `${window.location.pathname}${serializeUrlState(state)}${window.location.hash}`
}

/**
 * Synchronise l'état de navigation et l'URL, dans les deux sens.
 * Onglet/espace : pushState (le bouton Précédent fonctionne) ;
 * fenêtre, panneau, filtres : replaceState (pas de pollution de l'historique).
 */
export function useUrlSync(): void {
  useLayoutEffect(() => {
    readUrlIntoStore(window.location.search)
    let prev = selectUrlState(useStore.getState())
    // URL bricolée : on la remplace par sa forme canonique.
    writeHistory('replace', urlFor(prev))
    let applying = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const unsubscribe = useStore.subscribe((s) => {
      const next = selectUrlState(s)
      const same = (Object.keys(next) as (keyof UrlState)[]).every((k) => next[k] === prev[k])
      if (same) return
      const action = applying ? 'none' : historyAction(prev, next)
      const typing = next.filters.q !== prev.filters.q
      prev = next
      clearTimeout(timer)
      if (action === 'none') return
      // La saisie dans la recherche ne doit pas écrire l'URL à chaque lettre.
      if (action === 'replace' && typing) {
        timer = setTimeout(() => writeHistory('replace', urlFor(selectUrlState(useStore.getState()))), SEARCH_DEBOUNCE_MS)
        return
      }
      writeHistory(action, urlFor(next))
    })

    const onPop = () => {
      applying = true
      try {
        readUrlIntoStore(window.location.search)
      } finally {
        applying = false
      }
    }
    window.addEventListener('popstate', onPop)
    return () => {
      unsubscribe()
      clearTimeout(timer)
      window.removeEventListener('popstate', onPop)
    }
  }, [])
}
