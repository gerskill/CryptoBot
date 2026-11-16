/** Dépôt des demandes de modification (`api/server.py::post_prediction_request`). */

export const REASON_LABEL: Record<string, string> = {
  nom_invalide: 'nom invalide : pm_ suivi de 3 à 30 lettres minuscules, chiffres ou _',
  nom_deja_pris: 'ce nom existe déjà',
  parent_inconnu: 'bras parent inconnu',
  bras_inconnu: 'bras inconnu',
  identique_au_parent: 'aucune différence avec le parent',
  variable_non_autorisee_pour_ce_role: 'cette variable ne s’applique pas à ce type de bras',
  fourchette_hors_bornes: 'fourchette hors bornes (entre 2 % et 98 %, minimum sous le maximum)',
  trop_de_forks: 'nombre maximal de forks atteint',
}

/** Dépôt d'une demande. Le jeton n'est gardé qu'en mémoire, le temps de la page. */
export async function sendRequest(body: Record<string, unknown>, token: string): Promise<string | null> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-CryptobBot': '1' }
  if (token) headers.Authorization = `Bearer ${token}`
  try {
    const res = await fetch('/api/prediction/requests', { method: 'POST', headers, body: JSON.stringify(body) })
    if (res.ok) return null
    const detail = ((await res.json().catch(() => ({}))) as { detail?: string }).detail
    if (res.status === 401) return 'jeton requis ou invalide (DASHBOARD_TOKEN)'
    if (detail?.endsWith('_hors_bornes') && !REASON_LABEL[detail]) return 'valeur hors des bornes autorisées'
    return REASON_LABEL[detail ?? ''] ?? detail ?? `HTTP ${res.status}`
  } catch (error) {
    return error instanceof Error ? error.message : String(error)
  }
}

