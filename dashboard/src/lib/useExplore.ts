import { useCallback, useDeferredValue, useMemo, useState } from 'react'
import {
  ariaSortOf, matchesWords, nextSort, queryWords, searchText, sortRows,
  type Accessors, type SortState,
} from './explore'

type Options<T> = {
  rows: readonly T[]
  /** Champs de la recherche texte. Constante de module ou `useCallback` : sa
   *  référence pilote le précalcul du texte normalisé. */
  searchOf: (row: T) => ReadonlyArray<string | number | null | undefined>
  accessors: Accessors<T>
  /** Filtre propre au panneau (statut, bras…), appliqué en plus de la recherche. */
  predicate?: (row: T) => boolean
  /** Vrai quand le filtre du panneau n'est pas à sa valeur par défaut. */
  filterActive?: boolean
  /** Remet à zéro les filtres que le panneau possède lui-même. */
  onReset?: () => void
  /** Colonnes qui se trient d'abord en décroissant (P&L, mise, durée…). */
  descFirst?: readonly string[]
  /** Requête portée ailleurs (l'URL) : sinon le hook garde la sienne. */
  query?: string
  onQuery?: (query: string) => void
}

/**
 * État d'exploration d'une liste : recherche, tri, compteur, réinitialisation.
 * Le panneau garde la main sur ses filtres propres (`predicate`) ; le hook ne
 * possède que la requête et le tri, communs à toutes les listes.
 *
 * Le texte normalisé de chaque ligne est calculé une fois par changement de
 * données, pas à chaque frappe ; la requête est différée pour que la saisie
 * reste fluide sur de longues listes.
 */
export function useExplore<T>({
  rows, searchOf, accessors, predicate, filterActive = false, onReset, descFirst,
  query: controlledQuery, onQuery,
}: Options<T>) {
  const [ownQuery, setOwnQuery] = useState('')
  const query = controlledQuery ?? ownQuery
  const setQuery = onQuery ?? setOwnQuery
  const [sort, setSort] = useState<SortState | null>(null)
  const deferredQuery = useDeferredValue(query)

  const index = useMemo(() => rows.map((row) => searchText(searchOf(row))), [rows, searchOf])
  const visible = useMemo(() => {
    const words = queryWords(deferredQuery)
    const matching = rows.filter((row, i) => (predicate ? predicate(row) : true) && matchesWords(index[i], words))
    return sortRows(matching, accessors, sort)
  }, [rows, index, deferredQuery, predicate, accessors, sort])

  const toggleSort = useCallback(
    (key: string) => setSort((current) => nextSort(current, key, descFirst?.includes(key) ? 'desc' : 'asc')),
    [descFirst],
  )
  const ariaSort = (key: string) => ariaSortOf(sort, key)
  const reset = () => {
    setQuery('')
    setSort(null)
    onReset?.()
  }

  return {
    query, setQuery, sort, toggleSort, ariaSort, reset, visible,
    total: rows.length,
    isFiltered: query.trim() !== '' || sort !== null || filterActive,
  }
}
