import { withBom } from './csv'

const REVOKE_DELAY_MS = 10_000

/**
 * Déclenche le téléchargement (seule partie impure de l'export). Renvoie
 * `false` si le navigateur refuse (Blob/URL indisponibles) pour que l'appelant
 * le dise au lieu d'échouer en silence.
 */
export function downloadCsv(filename: string, csv: string): boolean {
  try {
    const blob = new Blob([withBom(csv)], { type: 'text/csv;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS)
    return true
  } catch {
    return false
  }
}
