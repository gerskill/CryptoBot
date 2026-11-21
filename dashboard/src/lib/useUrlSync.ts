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
    window.history.replaceState(null, '', urlFor(prev))
    let applying = false

    const unsubscribe = useStore.subscribe((s) => {
      const next = selectUrlState(s)
      const same = (Object.keys(next) as (keyof UrlState)[]).every((k) => next[k] === prev[k])
      if (same) return
      const action = applying ? 'none' : historyAction(prev, next)
      prev = next
      if (action === 'none') return
      window.history[action === 'push' ? 'pushState' : 'replaceState'](null, '', urlFor(next))
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
      window.removeEventListener('popstate', onPop)
    }
  }, [])
}
