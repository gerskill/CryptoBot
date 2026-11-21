import type { DedupeMemory } from './alerts'

const KEY = 'cryptobot.alerts.memory.v1'
const EMPTY: DedupeMemory = { lastSeen: {}, lastNotifiedAt: 0 }

/** Mémoire anti-doublon gardée le temps de l'onglet : un rechargement ne re-notifie pas. */
export function loadMemory(): DedupeMemory {
  try {
    const raw = JSON.parse(sessionStorage.getItem(KEY) ?? 'null') as Partial<DedupeMemory> | null
    if (!raw || typeof raw.lastSeen !== 'object' || raw.lastSeen === null) return EMPTY
    const seen = Object.entries(raw.lastSeen).filter(([, at]) => typeof at === 'number')
    return { lastSeen: Object.fromEntries(seen), lastNotifiedAt: Number(raw.lastNotifiedAt) || 0 }
  } catch {
    return EMPTY
  }
}

export function saveMemory(memory: DedupeMemory): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(memory))
  } catch {
    /* navigation privée : la mémoire reste valable pour la page seulement */
  }
}

export function clearMemory(): void {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    /* rien à effacer */
  }
}
