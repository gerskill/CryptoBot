import { describe, expect, it } from 'vitest'
import { csvFilename, escapeCell, toCsv, UTF8_BOM, withBom } from './csv'

describe('escapeCell', () => {
  it('laisse passer un texte simple et les nombres', () => {
    expect(escapeCell('abc')).toBe('abc')
    expect(escapeCell(12.5)).toBe('12.5')
    expect(escapeCell(-3)).toBe('-3')
  })
  it('vide pour null, undefined et nombres non finis', () => {
    expect(escapeCell(null)).toBe('')
    expect(escapeCell(undefined)).toBe('')
    expect(escapeCell(Infinity)).toBe('')
    expect(escapeCell(NaN)).toBe('')
  })
  it('entoure de guillemets et double les guillemets', () => {
    expect(escapeCell('dit "oui"')).toBe('"dit ""oui"""')
  })
  it('entoure virgules et retours à la ligne', () => {
    expect(escapeCell('a,b')).toBe('"a,b"')
    expect(escapeCell('a\nb')).toBe('"a\nb"')
    expect(escapeCell('a\r\nb')).toBe('"a\r\nb"')
  })
  it.each(['=1+1', '+SUM(A1)', '-2+3', '@cmd'])('neutralise la formule %s', (cell) => {
    expect(escapeCell(cell)).toBe(`'${cell}`)
  })
  it('neutralise puis protège une formule contenant une virgule', () => {
    expect(escapeCell('=A1,B1')).toBe('"\'=A1,B1"')
  })
  it('ne préfixe pas un nombre négatif', () => {
    expect(escapeCell(-0.5)).toBe('-0.5')
  })
  it('met les booléens en texte', () => {
    expect(escapeCell(true)).toBe('true')
  })
})

describe('toCsv', () => {
  it('écrit l\'en-tête puis les lignes en CRLF', () => {
    const csv = toCsv(
      [
        { header: 'bras', value: (r: { a: string; n: number }) => r.a },
        { header: 'pnl', value: (r) => r.n },
      ],
      [{ a: 'x,y', n: -1 }],
    )
    expect(csv).toBe('bras,pnl\r\n"x,y",-1\r\n')
  })
  it('garde l\'en-tête seul quand il n\'y a aucune ligne', () => {
    expect(toCsv([{ header: 'a', value: () => 1 }], [])).toBe('a\r\n')
  })
})

describe('withBom / csvFilename', () => {
  it('préfixe le BOM UTF-8', () => {
    expect(withBom('a')).toBe(`${UTF8_BOM}a`)
    expect(UTF8_BOM).toBe('﻿')
  })
  it('date le nom de fichier (UTC)', () => {
    expect(csvFilename('trades', new Date('2026-11-21T10:00:00Z'))).toBe('cryptobot-trades-2026-11-21.csv')
  })
})
