import { useMemo, useRef } from 'react'
import { motion } from 'framer-motion'
import { useStore } from '../lib/store'
import { SHORTCUT_DOCS } from '../lib/shortcuts'
import { saveShortcutsEnabled } from '../lib/useShortcuts'
import { useFocusTrap } from '../lib/useFocusTrap'

function HelpDialog({ onClose }: { onClose: () => void }) {
  const enabled = useStore((s) => s.shortcutsEnabled)
  const setEnabled = useStore((s) => s.setShortcutsEnabled)
  const announce = useStore((s) => s.announce)
  const ref = useRef<HTMLDivElement>(null)
  useFocusTrap(ref, onClose)

  const toggle = (value: boolean) => {
    setEnabled(value)
    saveShortcutsEnabled(value)
    announce(value ? 'Raccourcis clavier activés' : 'Raccourcis clavier désactivés')
  }

  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-void/80 px-4 py-[8vh]"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }}
      onMouseDown={(ev) => { if (ev.target === ev.currentTarget) onClose() }}
    >
      <motion.div
        ref={ref} role="dialog" aria-modal="true" aria-labelledby="help-title"
        initial={{ y: -8, opacity: 0 }} animate={{ y: 0, opacity: 1 }}
        className="w-full max-w-lg rounded-lg bg-surface p-4 ring-1 ring-edge-strong"
      >
        <div className="flex items-center justify-between gap-2">
          <h2 id="help-title" className="text-[11px] font-semibold uppercase tracking-[0.2em] text-dim">
            Raccourcis clavier
          </h2>
          <button type="button" onClick={onClose} autoFocus
            className="h-11 min-w-11 rounded-md px-2 font-mono text-[11px] uppercase tracking-wider text-muted ring-1 ring-edge hover:text-ink">
            Fermer
          </button>
        </div>
        <dl className="mt-3 space-y-2.5">
          {SHORTCUT_DOCS.map((doc) => (
            <div key={doc.label} className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <dt className="text-xs text-muted">{doc.label}</dt>
              <dd className="flex shrink-0 gap-1">
                {doc.keys.map((k) => (
                  <kbd key={k} className="rounded bg-raised px-1.5 py-0.5 font-mono text-[11px] text-ink ring-1 ring-edge">{k}</kbd>
                ))}
              </dd>
            </div>
          ))}
        </dl>
        <p className="mt-3 text-[11px] leading-snug text-dim">
          Les raccourcis à une touche ne s’activent jamais dans un champ de saisie et ne touchent
          pas aux raccourcis du navigateur (Ctrl/Cmd + lettre).
        </p>
        <label className="mt-3 flex min-h-11 cursor-pointer items-center gap-3 text-xs text-ink">
          <input type="checkbox" checked={enabled} onChange={(ev) => toggle(ev.target.checked)} className="h-5 w-5 accent-[var(--color-gem)]" />
          Raccourcis clavier activés
        </label>
      </motion.div>
    </motion.div>
  )
}

export function ShortcutsHelp() {
  const open = useStore((s) => s.helpOpen)
  const setOpen = useStore((s) => s.setHelpOpen)
  const close = useMemo(() => () => setOpen(false), [setOpen])
  return open ? <HelpDialog onClose={close} /> : null
}
