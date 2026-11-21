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

/**
 * Tous les mots de la requête doivent figurer dans l'un des champs (ET).
 * « btc 100k » retrouve « BTC au-dessus de 100k » mais pas un marché ETH.
 */
export function matchesQuery(
  fields: ReadonlyArray<string | number | null | undefined>,
  query: string,
): boolean {
  const words = normalize(query).split(/\s+/).filter(Boolean)
  if (words.length === 0) return true
  const haystack = fields
    .filter((f): f is string | number => f != null)
    .map((f) => normalize(String(f)))
    .join(' ')
  return words.every((word) => haystack.includes(word))
}

/** Clic sur une colonne : nouvelle colonne en ascendant, même colonne on bascule. */
export function nextSort(current: SortState | null, key: string): SortState {
  if (current?.key === key) return { key, dir: current.dir === 'asc' ? 'desc' : 'asc' }
  return { key, dir: 'asc' }
}

export function ariaSortOf(
  sort: SortState | null,
  key: string,
): 'ascending' | 'descending' | 'none' {
  if (sort?.key !== key) return 'none'
  return sort.dir === 'asc' ? 'ascending' : 'descending'
}

function compareValues(a: NonNullable<Sortable>, b: NonNullable<Sortable>): number {
  if (typeof a === 'number' && typeof b === 'number') return a - b
  if (typeof a === 'boolean' && typeof b === 'boolean') return Number(a) - Number(b)
  return String(a).localeCompare(String(b), 'fr', { numeric: true, sensitivity: 'base' })
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
      const aMissing = a.value == null
      const bMissing = b.value == null
      if (aMissing || bMissing) {
        return aMissing === bMissing ? a.index - b.index : aMissing ? 1 : -1
      }
      const order = compareValues(a.value as NonNullable<Sortable>, b.value as NonNullable<Sortable>)
      return order !== 0 ? sign * order : a.index - b.index
    })
    .map((entry) => entry.row)
}

export const countLabel = (shown: number, total: number): string => `${shown} sur ${total}`
