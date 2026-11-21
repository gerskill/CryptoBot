import { useCallback, useState } from 'react'
import {
  ariaSortOf, matchesQuery, nextSort, sortRows,
  type Accessors, type SortState,
} from './explore'

type Options<T> = {
  rows: readonly T[]
  /** Champs dans lesquels la recherche texte cherche. */
  searchOf: (row: T) => ReadonlyArray<string | number | null | undefined>
  accessors: Accessors<T>
  /** Filtre propre au panneau (statut, bras…), appliqué en plus de la recherche. */
  predicate?: (row: T) => boolean
  /** Vrai quand le filtre du panneau n'est pas à sa valeur par défaut. */
  filterActive?: boolean
  /** Remet à zéro les filtres que le panneau possède lui-même. */
  onReset?: () => void
}

/**
 * État d'exploration d'une liste : recherche, tri, compteur, réinitialisation.
 * Le panneau garde la main sur ses filtres propres (`predicate`) ; le hook ne
 * possède que la requête et le tri, communs à toutes les listes.
 */
export function useExplore<T>({ rows, searchOf, accessors, predicate, filterActive = false, onReset }: Options<T>) {
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<SortState | null>(null)

  const matching = rows.filter((row) => (predicate ? predicate(row) : true) && matchesQuery(searchOf(row), query))
  const visible = sortRows(matching, accessors, sort)

  const toggleSort = useCallback((key: string) => setSort((current) => nextSort(current, key)), [])
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
