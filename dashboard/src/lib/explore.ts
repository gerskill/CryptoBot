/**
 * Filtre, recherche et tri des listes du tableau de bord.
 *
 * Fonctions PURES : aucune ne mute son entrée, aucune ne touche au DOM. Les
 * quatre listes (positions, bras, marchés, paris) partagent ces règles ;
 * sans elles, chaque panneau aurait sa propre idée de « trier » et de
 * « chercher », et les écarts se verraient à l'usage.
 */

export type SortDir = 'asc' | 'desc'
export type SortState = { key: string; dir: SortDir }
/** Valeur triable. `null`/`undefined` = donnée absente, rangée en dernier. */
export type Sortable = string | number | boolean | null | undefined
export type Accessors<T> = Record<string, (row: T) => Sortable>

/** Minuscules, sans accents : « Règle » se trouve en tapant « regle ». */
export function normalize(text: string): string {
  return text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim()
}

/** Mots de la requête, normalisés. Vide = pas de filtre. */
export function queryWords(query: string): string[] {
  return normalize(query).split(/\s+/).filter(Boolean)
}

/** Texte de recherche d'une ligne, normalisé UNE fois (précalculé par le hook). */
export function searchText(fields: ReadonlyArray<string | number | null | undefined>): string {
  return fields
    .filter((f): f is string | number => f != null)
    .map((f) => normalize(String(f)))
    .join(' ')
}

/** Tous les mots doivent figurer dans le texte précalculé (ET). */
export function matchesWords(text: string, words: readonly string[]): boolean {
  return words.every((word) => text.includes(word))
}

/**
 * Tous les mots de la requête doivent figurer dans l'un des champs (ET).
 * « btc 100k » retrouve « BTC au-dessus de 100k » mais pas un marché ETH.
 */
export function matchesQuery(
  fields: ReadonlyArray<string | number | null | undefined>,
  query: string,
): boolean {
  return matchesWords(searchText(fields), queryWords(query))
}

/**
 * Clic sur une colonne : nouvelle colonne dans son sens initial (`initial`,
 * décroissant pour un P&L où l'on cherche le plus gros d'abord), même colonne
 * on bascule.
 */
export function nextSort(current: SortState | null, key: string, initial: SortDir = 'asc'): SortState {
  if (current?.key === key) return { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
  return { key, dir: initial }
}

export function ariaSortOf(
  sort: SortState | null,
  key: string,
): 'ascending' | 'descending' | 'none' {
  if (sort?.key !== key) return 'none'
  return sort.dir === 'asc' ? 'ascending' : 'descending'
}

const COLLATOR = new Intl.Collator('fr', { numeric: true, sensitivity: 'base' })

/** Absent = null, undefined, NaN ou ±Infinity : aucune ne se compare honnêtement. */
function isMissing(value: Sortable): value is null | undefined {
  return value == null || (typeof value === 'number' && !Number.isFinite(value))
}

function compareValues(a: NonNullable<Sortable>, b: NonNullable<Sortable>): number {
  if (typeof a === 'string' || typeof b === 'string') return COLLATOR.compare(String(a), String(b))
  const x = Number(a)
  const y = Number(b)
  return x < y ? -1 : x > y ? 1 : 0
}

/**
 * Tri stable. Les valeurs absentes restent EN DERNIER dans les deux sens :
 * un « — » ne doit jamais coiffer la liste en tri descendant.
 */
export function sortRows<T>(rows: readonly T[], accessors: Accessors<T>, sort: SortState | null): T[] {
  const get = sort ? accessors[sort.key] : undefined
  if (!sort || !get) return [...rows]
  const sign = sort.dir === 'asc' ? 1 : -1
  return rows
    .map((row, index) => ({ row, index, value: get(row) }))
    .sort((a, b) => {
      const aMissing = isMissing(a.value)
      const bMissing = isMissing(b.value)
      if (aMissing || bMissing) {
        return aMissing === bMissing ? a.index - b.index : aMissing ? 1 : -1
      }
      const order = compareValues(a.value as NonNullable<Sortable>, b.value as NonNullable<Sortable>)
      return order !== 0 ? sign * order : a.index - b.index
    })
    .map((entry) => entry.row)
}

/**
 * Fige l'ordre d'affichage sur une liste d'identifiants, valeurs à jour.
 * Sert aux données temps réel : sans ça, un clic peut tomber sur une autre
 * ligne parce que la liste s'est réordonnée sous le curseur.
 */
export function keepOrder<T>(rows: readonly T[], idOf: (row: T) => string, ids: readonly string[]): T[] {
  const rank = new Map(ids.map((id, index) => [id, index]))
  const known = rows.filter((row) => rank.has(idOf(row)))
  const fresh = rows.filter((row) => !rank.has(idOf(row)))
  return [...known.sort((a, b) => (rank.get(idOf(a)) ?? 0) - (rank.get(idOf(b)) ?? 0)), ...fresh]
}

export const countLabel = (shown: number, total: number): string => `${shown} sur ${total}`
