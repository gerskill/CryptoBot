import { useEffect, useId, useRef, type KeyboardEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion } from 'framer-motion'

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'

/**
 * Tiroir latéral de détail.
 *
 * Modal au sens strict : `role="dialog"` + `aria-modal`, focus piégé, Échap
 * et clic sur le voile ferment. Rendu dans `document.body` — les cartes de
 * positions portent des transformées framer-motion qui casseraient un
 * `position: fixed` imbriqué. Sur mobile il prend toute la largeur.
 */
export function Drawer({
  open,
  onClose,
  title,
  subtitle,
  children,
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

function DrawerPanel({
  onClose, title, subtitle, children,
}: { onClose: () => void; title: string; subtitle?: string; children: ReactNode }) {
  const titleId = useId()
  const panel = useRef<HTMLDivElement>(null)
  const closeButton = useRef<HTMLButtonElement>(null)

  useEffect(() => closeButton.current?.focus(), [])

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape') {
      event.stopPropagation()
      onClose()
      return
    }
    if (event.key !== 'Tab' || !panel.current) return
    const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)]
    if (items.length === 0) return
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    // Le piège ne se referme que sur les bords : le Tab natif fait le reste.
    if (event.shiftKey && (active === first || !panel.current.contains(active))) {
      event.preventDefault()
      last.focus()
    } else if (!event.shiftKey && (active === last || !panel.current.contains(active))) {
      event.preventDefault()
      first.focus()
    }
  }

  return (
    <motion.div
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
        onKeyDown={onKeyDown}
        onClick={(event) => event.stopPropagation()}
        initial={{ x: '100%' }}
        animate={{ x: 0 }}
        exit={{ x: '100%' }}
        transition={{ type: 'spring', stiffness: 380, damping: 36 }}
        className="flex h-full w-full flex-col bg-surface ring-1 ring-edge-strong sm:max-w-md"
      >
        <header className="flex shrink-0 items-start justify-between gap-3 border-b border-edge/60 px-4 py-3">
          <div className="min-w-0">
            <h2 id={titleId} className="break-words text-base font-semibold text-ink">{title}</h2>
            {subtitle && <p className="mt-0.5 break-words text-[11px] text-muted">{subtitle}</p>}
          </div>
          <button
            ref={closeButton}
            type="button"
            onClick={onClose}
            className="-mr-2 flex min-h-11 min-w-11 shrink-0 items-center justify-center rounded-md text-xs text-muted transition-colors hover:text-ink sm:min-h-8 sm:min-w-8"
          >
            <span aria-hidden="true" className="text-lg leading-none">×</span>
            <span className="sr-only">Fermer</span>
          </button>
        </header>
        <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-4 py-4">{children}</div>
      </motion.div>
    </motion.div>
  )
}
