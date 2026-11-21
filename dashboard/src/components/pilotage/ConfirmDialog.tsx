import { useEffect, useId, useRef } from 'react'

type Props = {
  title: string
  /** La conséquence, dite en clair : c'est ce qui est lu avant de confirmer. */
  consequence: string
  confirmLabel: string
  busy: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * Confirmation d'une action destructrice. `alertdialog` : le lecteur d'écran
 * annonce titre et conséquence. Le focus part sur ANNULER (jamais sur l'action),
 * reste piégé dans la boîte, et revient à l'élément d'origine à la fermeture.
 */
export function ConfirmDialog({ title, consequence, confirmLabel, busy, onConfirm, onCancel }: Props) {
  const titleId = useId()
  const descId = useId()
  const cancelRef = useRef<HTMLButtonElement>(null)
  const confirmRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const origine = document.activeElement as HTMLElement | null
    cancelRef.current?.focus()
    return () => origine?.focus?.()
  }, [])

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === 'Escape' && !busy) {
      event.stopPropagation()
      onCancel()
      return
    }
    if (event.key !== 'Tab') return
    // Deux boutons seulement : Tab et Maj+Tab font la boucle entre eux.
    const [premier, dernier] = [cancelRef.current, confirmRef.current]
    if (!premier || !dernier) return
    if (event.shiftKey && document.activeElement === premier) { event.preventDefault(); dernier.focus() }
    else if (!event.shiftKey && document.activeElement === dernier) { event.preventDefault(); premier.focus() }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-void/80 p-4 sm:items-center">
      <div role="alertdialog" aria-modal="true" aria-labelledby={titleId} aria-describedby={descId}
        onKeyDown={onKeyDown}
        className="w-full max-w-md rounded-lg bg-surface p-5 ring-1 ring-warn/50">
        <h2 id={titleId} className="text-sm font-semibold text-warn">{title}</h2>
        <p id={descId} className="mt-2 text-[13px] leading-snug text-muted">{consequence}</p>
        <div className="mt-5 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button ref={cancelRef} type="button" disabled={busy} onClick={onCancel}
            className="min-h-11 rounded-md bg-raised px-4 font-mono text-[11px] uppercase tracking-wider text-ink ring-1 ring-edge-strong hover:ring-gem/60 disabled:opacity-50">
            Annuler
          </button>
          <button ref={confirmRef} type="button" disabled={busy} onClick={onConfirm}
            className="min-h-11 rounded-md bg-warn/15 px-4 font-mono text-[11px] uppercase tracking-wider text-warn ring-1 ring-warn/50 hover:bg-warn/25 disabled:opacity-50">
            {busy ? 'Envoi en cours…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
