import type { Space } from './store'

/**
 * Table des raccourcis clavier globaux. Aucune touche seule ne se déclenche
 * avec Ctrl/Cmd/Alt (ça masquerait le navigateur), sauf Ctrl/Cmd+K pour la
 * palette, convention universelle.
 */

export type KeyInfo = {
  key: string
  /** Position physique (ex. « Digit1 ») : fiable sur AZERTY, où la touche 1 donne « & ». */
  code?: string
  ctrlKey: boolean
  metaKey: boolean
  altKey: boolean
  shiftKey: boolean
}

export type ShortcutAction =
  | { type: 'palette' }
  | { type: 'help' }
  | { type: 'tab'; index: number }
  | { type: 'space-toggle' }
  | { type: 'pending-g' }

/** Lettres « g puis… » : l'index est celui de l'onglet dans NavBar. */
const GOTO: Record<Space, readonly string[]> = {
  memecoins: ['a', 'd', 'l', 'w', 'c', 'b', 'p'],
  liquid: ['a', 'p', 'm', 'b', 'i'],
}
const MAX_TAB_INDEX = 5

/** Chiffre 1-9 pressé : par `key`, ou par `code` hors Maj (AZERTY non décalé). */
function digitOf(ev: KeyInfo): number | null {
  if (/^[1-9]$/.test(ev.key)) return Number(ev.key)
  const m = /^(?:Digit|Numpad)([1-9])$/.exec(ev.code ?? '')
  return m && !ev.shiftKey ? Number(m[1]) : null
}

export function resolveKey(ev: KeyInfo, ctx: { space: Space; pendingG: boolean }): ShortcutAction | null {
  const key = ev.key
  if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && key.toLowerCase() === 'k') return { type: 'palette' }
  if (ev.ctrlKey || ev.metaKey || ev.altKey) return null

  if (ctx.pendingG) {
    const index = GOTO[ctx.space].indexOf(key.toLowerCase())
    return index >= 0 ? { type: 'tab', index } : null
  }
  if (key === '/') return { type: 'palette' }
  if (key === '?') return { type: 'help' }
  if (key === 's') return { type: 'space-toggle' }
  if (key === 'g') return { type: 'pending-g' }
  const digit = digitOf(ev)
  if (digit !== null && digit <= MAX_TAB_INDEX) return { type: 'tab', index: digit - 1 }
  return null
}

/** Vrai si la frappe est destinée à un champ de saisie. */
export function isEditableTarget(el: EventTarget | null): boolean {
  if (!(el instanceof HTMLElement)) return false
  if (['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) return true
  if (el.isContentEditable || el.getAttribute('contenteditable') === 'true') return true
  return ['textbox', 'combobox', 'searchbox', 'spinbutton'].includes(el.getAttribute('role') ?? '')
}

/** Documentation affichée dans l'aide (touche « ? »). */
export const SHORTCUT_DOCS: readonly { keys: readonly string[]; label: string }[] = [
  { keys: ['Ctrl/Cmd', 'K'], label: 'Ouvrir la palette de commandes' },
  { keys: ['/'], label: 'Ouvrir la palette (hors champ de saisie)' },
  { keys: ['1', '2', '3', '4', '5'], label: 'Aller à l’onglet n° 1 à 5 de l’espace actif (AZERTY : sans Maj)' },
  { keys: ['g', 'puis lettre'], label: 'Aller à un onglet : a (Compte), d, l, w, c, b, p (Memecoins) ou a, p, m, b, i (Liquid)' },
  { keys: ['s'], label: 'Basculer entre les espaces Memecoins et Liquid' },
  { keys: ['?'], label: 'Afficher cette aide' },
  { keys: ['Échap'], label: 'Fermer la palette ou l’aide' },
]
