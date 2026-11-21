/** Export CSV : fonctions pures, aucune dépendance React ni DOM. */

export const UTF8_BOM = '﻿'
const EOL = '\r\n'
// Une cellule texte qui commence par l'un de ces caractères est interprétée
// comme une formule par Excel/Sheets : on la neutralise avec une apostrophe.
const FORMULA_STARTS = ['=', '+', '-', '@', '\t', '\r']
const NEEDS_QUOTES = /[",;\r\n]/

export type CsvValue = string | number | boolean | null | undefined
export type CsvColumn<T> = { header: string; value: (row: T) => CsvValue }

/** Un nombre reste un nombre (`-3` n'est pas une formule) ; seul le texte est préfixé. */
export function escapeCell(value: CsvValue): string {
  if (value === null || value === undefined) return ''
  if (typeof value === 'number') return Number.isFinite(value) ? String(value) : ''
  let text = String(value)
  if (FORMULA_STARTS.some((start) => text.startsWith(start))) text = `'${text}`
  return NEEDS_QUOTES.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

export function toCsv<T>(columns: CsvColumn<T>[], rows: readonly T[]): string {
  const head = columns.map((column) => escapeCell(column.header)).join(',')
  const lines = rows.map((row) => columns.map((column) => escapeCell(column.value(row))).join(','))
  return [head, ...lines].join(EOL) + EOL
}

/** Le BOM fait ouvrir le fichier en UTF-8 par Excel (accents des libellés). */
export function withBom(content: string): string {
  return UTF8_BOM + content
}

export function csvFilename(kind: string, now: Date = new Date()): string {
  return `cryptobot-${kind}-${now.toISOString().slice(0, 10)}.csv`
}
