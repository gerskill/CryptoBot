import {
  useCallback, useEffect, useRef, useState,
  type KeyboardEvent, type MouseEvent, type RefObject,
} from 'react'

export type OpenEvent = MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>

/**
 * Pilote un tiroir de détail : quel élément est ouvert, et surtout d'où.
 *
 * Le déclencheur est mémorisé à l'ouverture pour que le focus y revienne à la
 * fermeture — sans ça, un utilisateur au clavier retombe en haut de la page
 * après chaque consultation et perd sa place dans la liste. Une ligne clic
 * peut contenir un vrai bouton (`data-row-trigger`) : c'est lui qui reprend le
 * focus, une `<tr>` n'étant pas focalisable.
 */
export function useDrawer<T>(fallback?: RefObject<HTMLElement | null>) {
  const [item, setItem] = useState<T | null>(null)
  const trigger = useRef<HTMLElement | null>(null)
  const wasOpen = useRef(false)
  const toFallback = useRef(false)

  const openFrom = useCallback(
    (value: T) => (event: OpenEvent) => {
      const row = event.currentTarget
      trigger.current = row.querySelector<HTMLElement>('[data-row-trigger]') ?? row
      toFallback.current = false
      setItem(value)
    },
    [],
  )
  const close = useCallback(() => setItem(null), [])
  /** Fermeture quand le déclencheur n'a plus lieu d'être (élément disparu). */
  const closeToFallback = useCallback(() => {
    toFallback.current = true
    setItem(null)
  }, [])

  useEffect(() => {
    if (item !== null) {
      wasOpen.current = true
      return
    }
    if (!wasOpen.current) return
    wasOpen.current = false
    // Après le retrait du tiroir (et de l'inertie du fond), pas pendant.
    const frame = requestAnimationFrame(() => {
      const target = toFallback.current ? fallback?.current : trigger.current
      target?.focus()
    })
    return () => cancelAnimationFrame(frame)
  }, [item, fallback])

  return { item, openFrom, close, closeToFallback }
}

/** Props d'une ligne cliquable à la souris ; le clavier passe par le bouton de la ligne. */
export function rowClick(onOpen: (event: OpenEvent) => void) {
  return { onClick: onOpen }
}
