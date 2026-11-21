import { useCallback, useEffect, useRef, useState, type MouseEvent, type KeyboardEvent } from 'react'

/**
 * Pilote un tiroir de détail : quel élément est ouvert, et surtout d'où.
 *
 * Le déclencheur est mémorisé à l'ouverture pour que le focus y revienne à la
 * fermeture — sans ça, un utilisateur au clavier retombe en haut de la page
 * après chaque consultation et perd sa place dans la liste.
 */
export function useDrawer<T>() {
  const [item, setItem] = useState<T | null>(null)
  const trigger = useRef<HTMLElement | null>(null)
  const wasOpen = useRef(false)

  const openFrom = useCallback(
    (value: T) => (event: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>) => {
      trigger.current = event.currentTarget
      setItem(value)
    },
    [],
  )
  const close = useCallback(() => setItem(null), [])

  useEffect(() => {
    if (item !== null) {
      wasOpen.current = true
      return
    }
    if (wasOpen.current) {
      wasOpen.current = false
      trigger.current?.focus()
    }
  }, [item])

  return { item, openFrom, close }
}

/**
 * Props d'une ligne cliquable : atteignable au clavier, activée par Entrée ou
 * Espace, comme un bouton. Une `<tr>` ne l'est pas par défaut.
 */
export function rowProps(onOpen: (event: MouseEvent<HTMLElement> | KeyboardEvent<HTMLElement>) => void) {
  return {
    tabIndex: 0,
    onClick: onOpen,
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => {
      if (event.target !== event.currentTarget) return
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        onOpen(event)
      }
    },
  }
}
