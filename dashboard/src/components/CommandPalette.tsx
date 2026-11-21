import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { motion } from 'framer-motion'
import { useStore } from '../lib/store'
import { buildDestinations } from '../lib/destinations'
import type { Destination } from '../lib/destinations'
import { rankByQuery } from '../lib/fuzzy'
import { navigateTo } from '../lib/navigate'
import { loadRecents, pushRecent, saveRecents } from '../lib/recents'
import { useFocusTrap } from '../lib/useFocusTrap'

type Row = { dest: Destination; group: string }

/** Requête vide : récents d'abord, puis tout. Sinon : résultats classés. */
function buildRows(query: string, all: Destination[], recents: string[]): Row[] {
  if (query.trim()) return rankByQuery(query, all).map((dest) => ({ dest, group: dest.group }))
  const recent = recents.map((id) => all.find((d) => d.id === id)).filter((d): d is Destination => !!d)
  return [
    ...recent.map((dest) => ({ dest, group: 'Récents' })),
    ...all.map((dest) => ({ dest, group: dest.group })),
  ]
}

function PaletteDialog({ onClose }: { onClose: () => void }) {
  const arms = useStore((s) => s.state.arms)
  const positions = useStore((s) => s.state.positions)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [recents, setRecents] = useState(loadRecents)
  const dialogRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listId = useId()
  useFocusTrap(dialogRef, onClose)

  const all = useMemo(() => buildDestinations({
    arms: (arms ?? []).map((a) => a.name),
    positions: (positions ?? []).map((p) => p.symbol),
  }), [arms, positions])
  const rows = useMemo(() => buildRows(query, all, recents), [query, all, recents])
  const current = rows.length ? Math.min(active, rows.length - 1) : -1
  const optionId = (i: number) => `${listId}-opt-${i}`

  useEffect(() => { inputRef.current?.focus() }, [])
  useEffect(() => {
    document.getElementById(optionId(current))?.scrollIntoView?.({ block: 'nearest' })
  }, [current]) // eslint-disable-line react-hooks/exhaustive-deps

  const choose = (row: Row | undefined) => {
    if (!row) return
    const next = pushRecent(recents, row.dest.id)
    setRecents(next)
    saveRecents(next)
    navigateTo(row.dest.target, row.dest.label)
    onClose()
  }

  const onKeyDown = (ev: React.KeyboardEvent) => {
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      ev.preventDefault()
      if (!rows.length) return
      const step = ev.key === 'ArrowDown' ? 1 : -1
      setActive((current + step + rows.length) % rows.length)
    } else if (ev.key === 'Enter') {
      ev.preventDefault()
      choose(rows[current])
    }
  }

  let lastGroup = ''
  return (
    <motion.div
      className="fixed inset-0 z-50 flex items-start justify-center bg-void/80 px-4 pt-[10vh]"
      initial={{ opacity: 0 }} animate={{ opacity: 1 }}
      onMouseDown={(ev) => { if (ev.target === ev.currentTarget) onClose() }}
    >
      <motion.div
        ref={dialogRef}
        role="dialog" aria-modal="true" aria-label="Palette de commandes"
        initial={{ y: -8, opacity: 0 }} animate={{ y: 0, opacity: 1 }}
        onKeyDown={onKeyDown}
        className="w-full max-w-xl overflow-hidden rounded-lg bg-surface ring-1 ring-edge-strong"
      >
        <div className="flex items-center gap-2 border-b border-edge/60 px-3">
          <input
            ref={inputRef} value={query}
            onChange={(ev) => { setQuery(ev.target.value); setActive(0) }}
            role="combobox" aria-expanded="true" aria-controls={listId} aria-autocomplete="list"
            aria-activedescendant={current >= 0 ? optionId(current) : undefined}
            aria-label="Aller à : onglet, panneau, bras, position"
            placeholder="Aller à… (onglet, bras, position)"
            className="h-12 min-w-0 flex-1 bg-transparent text-sm text-ink placeholder:text-dim focus:outline-none"
          />
          <button type="button" onClick={onClose} aria-label="Fermer la palette"
            className="h-11 min-w-11 rounded-md font-mono text-[11px] uppercase tracking-wider text-muted hover:text-ink">
            Échap
          </button>
        </div>
        <ul id={listId} role="listbox" aria-label="Destinations" className="max-h-[60vh] overflow-y-auto py-1">
          {rows.map((row, i) => {
            const header = row.group !== lastGroup ? row.group : null
            lastGroup = row.group
            return (
              <li key={`${row.group}-${row.dest.id}`} role="presentation">
                {header && (
                  <div role="presentation" className="px-4 pb-1 pt-2 text-[11px] uppercase tracking-[0.15em] text-dim">
                    {header}
                  </div>
                )}
                <div
                  id={optionId(i)} role="option" aria-selected={i === current}
                  onClick={() => choose(row)} onMouseMove={() => setActive(i)}
                  className={`flex min-h-11 cursor-pointer items-center justify-between gap-3 px-4 py-2 text-sm ${
                    i === current ? 'bg-raised text-ink' : 'text-muted'
                  }`}
                >
                  <span className="truncate">{row.dest.label}</span>
                  {row.dest.hint && <span className="shrink-0 text-[11px] text-dim">{row.dest.hint}</span>}
                </div>
              </li>
            )
          })}
        </ul>
        <div role="status" className="sr-only">
          {rows.length ? `${rows.length} destinations` : 'Aucun résultat'}
        </div>
        {!rows.length && <p className="px-4 py-6 text-center text-[11px] text-dim">Aucun résultat pour « {query} ».</p>}
        <p className="border-t border-edge/60 px-4 py-2 text-[11px] text-dim">
          ↑ ↓ pour choisir · Entrée pour aller · Échap pour fermer
        </p>
      </motion.div>
    </motion.div>
  )
}

/** Palette de commandes (Ctrl/Cmd+K ou « / »). Navigation seule, aucune action. */
export function CommandPalette() {
  const open = useStore((s) => s.paletteOpen)
  const setOpen = useStore((s) => s.setPaletteOpen)
  const close = useMemo(() => () => setOpen(false), [setOpen])
  return open ? <PaletteDialog onClose={close} /> : null
}
