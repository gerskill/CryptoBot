import { useEffect, useRef, useState, type RefObject } from 'react'
import { useDrawer, type OpenEvent } from './useDrawer'

/**
 * Tiroir sur des données TEMPS RÉEL : l'élément ouvert peut disparaître
 * (position clôturée) pendant la consultation. Dans ce cas le tiroir se ferme,
 * `gone` garde le dernier état connu pour l'annoncer, et le focus passe au
 * repli (la recherche). L'identifiant qui réapparaît ne rouvre rien : la
 * fermeture a vidé la sélection.
 */
export function useLiveDrawer<T>(
  list: readonly T[],
  idOf: (value: T) => string,
  fallback: RefObject<HTMLElement | null>,
) {
  const drawer = useDrawer<string>(fallback)
  const last = useRef<T | null>(null)
  const [gone, setGone] = useState<T | null>(null)
  const found = drawer.item === null ? null : (list.find((value) => idOf(value) === drawer.item) ?? null)

  useEffect(() => {
    if (found) last.current = found
  }, [found])

  const { item, closeToFallback } = drawer
  useEffect(() => {
    if (item !== null && found === null) {
      setGone(last.current)
      closeToFallback()
    }
  }, [item, found, closeToFallback])

  const openFrom = (id: string) => {
    const open = drawer.openFrom(id)
    return (event: OpenEvent) => {
      setGone(null)
      open(event)
    }
  }

  return { open: found !== null, item: found, gone, openFrom, close: drawer.close }
}
