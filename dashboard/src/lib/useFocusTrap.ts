import { useEffect } from 'react'
import type { RefObject } from 'react'

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), [href], select:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Piège le focus dans une boîte de dialogue : Tab/Maj+Tab bouclent, Échap
 * ferme, et le focus revient à l'élément qui avait la main avant l'ouverture.
 */
export function useFocusTrap(ref: RefObject<HTMLElement | null>, onClose: () => void): void {
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const node = ref.current
    if (!node) return undefined

    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        ev.preventDefault()
        ev.stopPropagation()
        onClose()
        return
      }
      if (ev.key !== 'Tab') return
      const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE))
      if (!items.length) return
      const first = items[0]
      const last = items[items.length - 1]
      const active = document.activeElement
      if (ev.shiftKey && (active === first || !node.contains(active))) {
        ev.preventDefault()
        last.focus()
      } else if (!ev.shiftKey && (active === last || !node.contains(active))) {
        ev.preventDefault()
        first.focus()
      }
    }
    node.addEventListener('keydown', onKey)
    return () => {
      node.removeEventListener('keydown', onKey)
      previous?.focus?.()
    }
  }, [ref, onClose])
}
