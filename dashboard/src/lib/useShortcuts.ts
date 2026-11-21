import { useEffect } from 'react'
import { useStore } from './store'
import { isEditableTarget, resolveKey } from './shortcuts'
import { goToTabIndex, toggleSpace } from './navigate'

const PENDING_G_MS = 1500
const SHORTCUTS_KEY = 'cryptobot.shortcuts.enabled'

export function loadShortcutsEnabled(): boolean {
  try {
    return localStorage.getItem(SHORTCUTS_KEY) !== 'off'
  } catch {
    return true
  }
}

export function saveShortcutsEnabled(enabled: boolean): void {
  try {
    localStorage.setItem(SHORTCUTS_KEY, enabled ? 'on' : 'off')
  } catch {
    // Préférence facultative : sans stockage, elle vaut pour la session.
  }
}

/**
 * Raccourcis globaux. Sans effet dans un champ de saisie (sauf Ctrl/Cmd+K),
 * quand une boîte de dialogue est ouverte, ou une fois désactivés.
 */
export function useGlobalShortcuts(): void {
  useEffect(() => {
    useStore.getState().setShortcutsEnabled(loadShortcutsEnabled())
    let pendingG = false
    let timer: ReturnType<typeof setTimeout> | undefined

    const onKey = (ev: KeyboardEvent) => {
      const st = useStore.getState()
      if (!st.shortcutsEnabled || ev.defaultPrevented || ev.isComposing) return
      if (st.paletteOpen || st.helpOpen) return
      if (isEditableTarget(ev.target) && !(ev.ctrlKey || ev.metaKey)) return

      const action = resolveKey(ev, { space: st.space, pendingG })
      pendingG = false
      clearTimeout(timer)
      if (!action) return
      ev.preventDefault()

      if (action.type === 'palette') st.setPaletteOpen(true)
      else if (action.type === 'help') st.setHelpOpen(true)
      else if (action.type === 'tab') goToTabIndex(action.index)
      else if (action.type === 'space-toggle') toggleSpace()
      else {
        pendingG = true
        st.announce('g : choisissez la lettre de l’onglet')
        timer = setTimeout(() => { pendingG = false }, PENDING_G_MS)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      clearTimeout(timer)
    }
  }, [])
}
