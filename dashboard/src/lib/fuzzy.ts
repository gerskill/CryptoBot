/** Recherche floue minimale : sous-séquence, insensible à la casse et aux accents. */

function fold(text: string): string {
  return text.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
}

const BONUS_PREFIX = 8
const BONUS_WORD_START = 4
const BONUS_CONSECUTIVE = 3
const PENALTY_GAP = 0.2

/**
 * Score d'une requête dans un texte, ou `null` si ce n'est pas une
 * sous-séquence. Plus c'est haut, mieux ça correspond. Requête vide : 0.
 */
export function fuzzyScore(query: string, text: string): number | null {
  const q = fold(query).replace(/\s+/g, '')
  if (!q) return 0
  const t = fold(text)
  let score = 0
  let from = 0
  let last = -2
  for (const ch of q) {
    const at = t.indexOf(ch, from)
    if (at === -1) return null
    if (at === 0) score += BONUS_PREFIX
    else if (/[\s\-_·/]/.test(t[at - 1])) score += BONUS_WORD_START
    if (at === last + 1) score += BONUS_CONSECUTIVE
    score += 1 - (at - from) * PENALTY_GAP
    last = at
    from = at + 1
  }
  return score
}

type Searchable = { label: string; keywords?: readonly string[] }

/** Filtre et trie par score décroissant (stable) ; sans requête, ordre d'origine. */
export function rankByQuery<T extends Searchable>(query: string, items: readonly T[]): T[] {
  if (!query.trim()) return [...items]
  const scored: { item: T; score: number; index: number }[] = []
  items.forEach((item, index) => {
    const own = fuzzyScore(query, item.label)
    const kw = (item.keywords ?? []).map((k) => fuzzyScore(query, k) ?? null)
    const best = Math.max(own ?? -Infinity, ...kw.map((s) => (s === null ? -Infinity : s - 2)))
    if (best > -Infinity) scored.push({ item, score: best, index })
  })
  scored.sort((a, b) => b.score - a.score || a.index - b.index)
  return scored.map((s) => s.item)
}
