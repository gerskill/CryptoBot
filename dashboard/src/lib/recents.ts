/** Destinations récentes de la palette : liste courte, plus récente en tête. */
const KEY = 'cryptobot.palette.recents'
export const MAX_RECENTS = 5

export function pushRecent(list: readonly string[], id: string, max = MAX_RECENTS): string[] {
  return [id, ...list.filter((x) => x !== id)].slice(0, max)
}

export function loadRecents(): string[] {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string').slice(0, MAX_RECENTS) : []
  } catch {
    return []
  }
}

export function saveRecents(list: readonly string[]): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(list))
  } catch {
    // Stockage indisponible (navigation privée) : les récents sont facultatifs.
  }
}
