import { useEffect, useId, useRef, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion, useIsPresent } from 'framer-motion'

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Tiroir latéral de détail.
 *
 * Modal au sens strict : `role="dialog"` + `aria-modal`, focus piégé, Échap
 * et clic sur le voile ferment, fond inerte et défilement verrouillé. Rendu
 * dans `document.body` — les cartes de positions portent des transformées
 * framer-motion qui casseraient un `position: fixed` imbriqué. Sur mobile il
 * prend toute la largeur.
 */
export function Drawer({
  open, onClose, title, subtitle, children,
}: {
  open: boolean
  onClose: () => void
  title: string
  subtitle?: string
  children: ReactNode
}) {
  return createPortal(
    <AnimatePresence>
      {open && (
        <DrawerPanel onClose={onClose} title={title} subtitle={subtitle}>
          {children}
        </DrawerPanel>
      )}
    </AnimatePresence>,
    document.body,
  )
}

/**
 * Échap et piège à Tab écoutés sur `document` : un clic dans le tiroir sur un
 * texte non focalisable renvoie le focus à `body`, et un écouteur posé sur le
 * panneau ne verrait plus rien.
 */
function useDialogKeys(panel: RefObject<HTMLElement | null>, active: boolean, onClose: () => void) {
  useEffect(() => {
    if (!active) return
    const onKeyDown = (event: KeyboardEvent) => {
      const root = panel.current
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab' || !root) return
      const items = [...root.querySelectorAll<HTMLElement>(FOCUSABLE)]
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      const inside = root.contains(document.activeElement)
      // Le piège ne se referme que sur les bords : le Tab natif fait le reste.
      if (!inside || (event.shiftKey && document.activeElement === first)) {
        event.preventDefault()
        ;(event.shiftKey ? last : first).focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [panel, active, onClose])
}

/**
 * Verrouille le défilement du fond et le rend inerte (aucun focus, aucun clic,
 * masqué aux lecteurs d'écran). Restauré dès que la sortie commence, avant la
 * fin de l'animation : le focus doit pouvoir revenir au déclencheur.
 */
function useBackgroundLock(own: RefObject<HTMLElement | null>, active: boolean) {
  useEffect(() => {
    if (!active) return
    const previousOverflow = document.body.style.overflow
    const inerted = [...document.body.children].filter(
      (node): node is HTMLElement => node instanceof HTMLElement && node !== own.current && !node.hasAttribute('inert'),
    )
    document.body.style.overflow = 'hidden'
    inerted.forEach((node) => node.setAttribute('inert', ''))
    return () => {
      document.body.style.overflow = previousOverflow
      inerted.forEach((node) => node.removeAttribute('inert'))
    }
  }, [own, active])
}

function DrawerPanel({
  onClose, title, subtitle, children,
}: { onClose: () => void; title: string; subtitle?: string; children: ReactNode }) {
  const titleId = useId()
  const backdrop = useRef<HTMLDivElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)
  const present = useIsPresent()

  useEffect(() => closeButton.current?.focus(), [])
  useDialogKeys(panel, present, onClose)
  useBackgroundLock(backdrop, present)

  return (
    <motion.div
      ref={backdrop}
      className="fixed inset-0 z-50 flex justify-end bg-void/70"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.15 }}
      onClick={onClose}
    >
      <motion.div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        initial={{ x: '100%' }}
        animate={{ x: 0 }}
        exit={{ x: '100%' }}
        transition={{ type: 'spring', stiffness: 380, damping: 36 }}
        className="flex h-full w-full flex-col bg-surface ring-1 ring-edge-strong sm:max-w-md"
      >
        <DrawerHeader titleId={titleId} title={title} subtitle={subtitle} onClose={onClose} closeRef={closeButton} />
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">{children}</div>
      </motion.div>
    </motion.div>
  )
}

function DrawerHeader({
  titleId, title, subtitle, onClose, closeRef,
}: {
  titleId: string
  title: string
  subtitle?: string
  onClose: () => void
  closeRef: RefObject<HTMLButtonElement | null>
}) {
  return (
    <header className="flex shrink-0 items-start justify-between gap-3 border-b border-edge/60 px-4 py-3">
      <div className="min-w-0">
        <h2 id={titleId} className="break-words text-base font-semibold text-ink">{title}</h2>
        {subtitle && <p className="mt-0.5 break-words text-[11px] text-muted">{subtitle}</p>}
      </div>
      <button
        ref={closeRef}
        type="button"
        onClick={onClose}
        className="-mr-2 flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-md text-xs text-muted transition-colors hover:text-ink"
      >
        <span aria-hidden="true" className="text-lg leading-none">×</span>
        <span className="sr-only">Fermer</span>
      </button>
    </header>
  )
}
