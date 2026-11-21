import { useStore } from './store'
import type { FilterKey } from './urlState'

/** Un filtre simple, porté par l'URL ; `fallback` est la valeur par défaut. */
export function useFilter(key: FilterKey, fallback: string): [string, (value: string) => void] {
  const value = useStore((s) => s.filters[key]) ?? fallback
  const setFilter = useStore((s) => s.setFilter)
  return [value, (next) => setFilter(key, next && next !== fallback ? next : null)]
}
