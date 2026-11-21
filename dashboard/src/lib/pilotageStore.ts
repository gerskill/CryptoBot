import { create } from 'zustand'
import { appendEntry, parseJournal, type JournalEntry } from './pilotage'

const KEY = 'cryptobot.pilotage.journal'

/** Le navigateur peut refuser le stockage (mode privé) : le journal reste alors en mémoire. */
function load(): JournalEntry[] {
  try {
    return parseJournal(localStorage.getItem(KEY))
  } catch {
    return []
  }
}

function save(journal: readonly JournalEntry[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(journal))
  } catch {
    /* stockage indisponible : on garde la mémoire seule */
  }
}

/** Journal local des dernières commandes. Jamais de jeton, seulement libellé et résultat. */
type PilotageStore = {
  journal: JournalEntry[]
  record: (entry: Omit<JournalEntry, 'id' | 't'>) => void
}

export const usePilotageStore = create<PilotageStore>((set) => ({
  journal: load(),
  record: (entry) =>
    set((s) => {
      const t = Date.now()
      const journal = appendEntry(s.journal, { ...entry, id: `${t}-${s.journal.length}`, t })
      save(journal)
      return { journal }
    }),
}))
