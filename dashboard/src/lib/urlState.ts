import type { LiquidView, Space, View } from './store'
import type { PerformanceWindow } from './types'
import { LIQUID_TABS, MEME_TABS, SPACES as SPACE_LIST } from './tabs'

/**
 * État de navigation porté par l'URL. Fonctions PURES : parsing tolérant
 * (une URL bricolée ne doit jamais faire planter l'écran), sérialisation
 * qui omet les valeurs par défaut pour garder des liens courts.
 */

export const SPACES: readonly Space[] = SPACE_LIST.map((s) => s.id)
export const MEME_VIEWS: readonly View[] = MEME_TABS.map((t) => t.id)
export const LIQUID_VIEWS: readonly LiquidView[] = LIQUID_TABS.map((t) => t.id)
export const WINDOWS: readonly PerformanceWindow[] = ['7d', '30d', 'all']

export type FilterKey = 'famille' | 'statut' | 'domaine' | 'q'
export type Filters = Readonly<Partial<Record<FilterKey, string>>>

export type UrlState = {
  space: Space
  view: View
  liquidView: LiquidView
  window: PerformanceWindow
  panel: string | null
  filters: Filters
}

export const DEFAULT_URL_STATE: UrlState = {
  space: 'memecoins', view: 'dashboard', liquidView: 'apercu', window: '7d', panel: null, filters: {},
}

const MAX_TEXT = 60
const FILTER_KEYS: readonly FilterKey[] = ['famille', 'statut', 'domaine', 'q']

/** Valeur par défaut d'un filtre : absente de l'URL, absente du store. */
export const FILTER_DEFAULTS: Readonly<Partial<Record<FilterKey, string>>> = { famille: 'prix', statut: 'tous' }

/** Valeurs permises par filtre ; `null` = texte libre nettoyé. */
const FILTER_CHOICES: Record<FilterKey, readonly string[] | null> = {
  famille: ['prix', 'sport', 'questions', 'autre'],
  statut: ['tous', 'ouvert', 'regle', 'indetermine'],
  domaine: null,
  q: null,
}

/** Retire les caractères de contrôle, espaces de bord, et borne la longueur. */
export function cleanText(raw: string): string {
  // eslint-disable-next-line no-control-regex
  return raw.replace(/[\u0000-\u001f\u007f]/g, '').trim().slice(0, MAX_TEXT)
}

function pick<T extends string>(raw: string | null, allowed: readonly T[]): T | undefined {
  return allowed.find((v) => v === raw)
}

function parseFilters(params: URLSearchParams): Filters {
  const out: Partial<Record<FilterKey, string>> = {}
  for (const key of FILTER_KEYS) {
    const value = cleanText(params.get(key) ?? '')
    const choices = FILTER_CHOICES[key]
    if (!value || value === FILTER_DEFAULTS[key] || (choices && !choices.includes(value))) continue
    out[key] = value
  }
  return out
}

/** Lit `?space=…&tab=…` ; ne renvoie que les champs valides. */
export function parseUrlState(search: string): Partial<UrlState> {
  let params: URLSearchParams
  try {
    params = new URLSearchParams(search)
  } catch {
    return {}
  }
  const out: { -readonly [K in keyof UrlState]?: UrlState[K] } = {}
  const space = pick(params.get('space'), SPACES)
  if (space) out.space = space

  const tab = params.get('tab')
  if (space === 'liquid') {
    const lv = pick(tab, LIQUID_VIEWS)
    if (lv) out.liquidView = lv
  } else {
    const v = pick(tab, MEME_VIEWS)
    if (v) out.view = v
  }
  const win = pick(params.get('window'), WINDOWS)
  if (win) out.window = win
  const panel = cleanText(params.get('panel') ?? '')
  if (panel) out.panel = panel
  const filters = parseFilters(params)
  if (Object.keys(filters).length) out.filters = filters
  return out
}

/** Construit la chaîne de recherche (« ?… » ou vide) en omettant les défauts. */
export function serializeUrlState(state: UrlState): string {
  const params = new URLSearchParams()
  const d = DEFAULT_URL_STATE
  if (state.space !== d.space) params.set('space', state.space)
  if (state.space === 'liquid') {
    if (state.liquidView !== d.liquidView) params.set('tab', state.liquidView)
  } else if (state.view !== d.view) {
    params.set('tab', state.view)
  }
  if (state.window !== d.window) params.set('window', state.window)
  if (state.panel) params.set('panel', state.panel)
  for (const key of FILTER_KEYS) {
    const value = state.filters[key]
    if (value && value !== FILTER_DEFAULTS[key]) params.set(key, value)
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

/** Onglet/espace : une entrée d'historique (Précédent). Le reste : remplacement. */
export function historyAction(prev: UrlState, next: UrlState): 'push' | 'replace' | 'none' {
  const navChanged =
    prev.space !== next.space ||
    (next.space === 'liquid' ? prev.liquidView !== next.liquidView : prev.view !== next.view)
  if (navChanged) return 'push'
  return serializeUrlState(prev) === serializeUrlState(next) ? 'none' : 'replace'
}
