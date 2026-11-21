import { useEffect, useId, useState } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import { selectUnread, useAlertStore } from '../../lib/alertStore'
import { AlertList } from './AlertList'
import { AlertSettingsForm } from './AlertSettingsForm'
import { BellIcon } from './icons'

/** Cloche + panneau : réglages des alertes et liste des dernières alertes. */
export function AlertCenter() {
  const [open, setOpen] = useState(false)
  const panelId = useId()
  const unread = useAlertStore(selectUnread)
  const enabled = useAlertStore((s) => s.settings.enabled)

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => event.key === 'Escape' && setOpen(false)
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open])

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        aria-label={`Alertes, ${unread} non lue${unread > 1 ? 's' : ''}`}
        aria-controls={panelId}
        className="flex min-h-11 min-w-11 shrink-0 items-center justify-center gap-1.5 rounded-lg px-3 text-[12px] text-muted ring-1 ring-edge/60 hover:text-ink"
      >
        <BellIcon />
        <span data-testid="alert-label" className="hidden sm:inline">Alertes</span>
        <span className="hidden font-mono text-[11px] text-dim sm:inline">{enabled ? '· actives' : '· off'}</span>
        {unread > 0 && (
          <span className="rounded bg-warn/15 px-1.5 font-mono text-[11px] font-semibold text-warn">
            {unread}
          </span>
        )}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            id={panelId}
            role="region"
            aria-label="Centre d'alertes"
            initial={{ opacity: 0, y: -4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -4 }}
            transition={{ duration: 0.15 }}
            className="absolute right-0 top-full z-30 mt-2 w-[min(24rem,calc(100vw-2.5rem))] space-y-4 rounded-lg bg-surface p-4 ring-1 ring-edge-strong"
          >
            <AlertSettingsForm />
            <div className="h-px bg-edge/60" />
            <AlertList />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
