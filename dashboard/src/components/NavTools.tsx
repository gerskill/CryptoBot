import { useStore } from '../lib/store'
import { copyViewLink } from '../lib/navigate'

const BTN =
  'inline-flex h-11 min-w-11 items-center justify-center gap-2 rounded-md px-3 font-mono text-[11px] uppercase tracking-wider text-muted ring-1 ring-edge transition-colors hover:text-ink'

/** Palette, aide des raccourcis, copie du lien de la vue. Cibles de 44 px. */
export function NavTools() {
  const setPalette = useStore((s) => s.setPaletteOpen)
  const setHelp = useStore((s) => s.setHelpOpen)
  return (
    <div className="flex items-center gap-1.5">
      <button type="button" className={BTN} onClick={() => setPalette(true)}
        aria-label="Ouvrir la palette de commandes" aria-haspopup="dialog">
        <span aria-hidden="true">⌕</span>
        <span className="hidden sm:inline">Rechercher</span>
        <kbd className="hidden rounded bg-raised px-1 text-[11px] normal-case lg:inline">Ctrl K</kbd>
      </button>
      <button type="button" className={BTN} onClick={() => setHelp(true)}
        aria-label="Afficher les raccourcis clavier" aria-haspopup="dialog">
        <span aria-hidden="true">?</span>
        <span className="hidden sm:inline">Raccourcis</span>
      </button>
      <button type="button" className={BTN} onClick={() => { void copyViewLink() }}
        aria-label="Copier le lien de cette vue">
        <span aria-hidden="true">⧉</span>
        <span className="hidden sm:inline">Copier le lien</span>
      </button>
    </div>
  )
}
