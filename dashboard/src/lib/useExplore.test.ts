import { describe, expect, it } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { useExplore } from './useExplore'

type Row = { name: string; pnl: number; open: boolean }
const ROWS: Row[] = [
  { name: 'pepe', pnl: 5, open: true },
  { name: 'doge', pnl: -2, open: false },
  { name: 'wif', pnl: 9, open: true },
]
const options = {
  rows: ROWS,
  searchOf: (r: Row) => [r.name],
  accessors: { name: (r: Row) => r.name, pnl: (r: Row) => r.pnl },
}

describe('useExplore', () => {
  it('au départ : tout, rien de filtré', () => {
    const { result } = renderHook(() => useExplore(options))
    expect(result.current.visible).toHaveLength(3)
    expect(result.current.total).toBe(3)
    expect(result.current.isFiltered).toBe(false)
  })

  it('la recherche filtre et signale un état filtré', () => {
    const { result } = renderHook(() => useExplore(options))
    act(() => result.current.setQuery('og'))
    expect(result.current.visible.map((r) => r.name)).toEqual(['doge'])
    expect(result.current.isFiltered).toBe(true)
  })

  it('toggleSort bascule asc/desc et expose aria-sort', () => {
    const { result } = renderHook(() => useExplore(options))
    act(() => result.current.toggleSort('pnl'))
    expect(result.current.visible.map((r) => r.pnl)).toEqual([-2, 5, 9])
    expect(result.current.ariaSort('pnl')).toBe('ascending')
    act(() => result.current.toggleSort('pnl'))
    expect(result.current.visible.map((r) => r.pnl)).toEqual([9, 5, -2])
    expect(result.current.ariaSort('pnl')).toBe('descending')
    expect(result.current.ariaSort('name')).toBe('none')
  })

  it('le prédicat externe s’applique en plus de la recherche', () => {
    const { result } = renderHook(() => useExplore({ ...options, predicate: (r) => r.open }))
    expect(result.current.visible.map((r) => r.name)).toEqual(['pepe', 'wif'])
    expect(result.current.total).toBe(3)
  })

  it('reset vide recherche et tri, et appelle onReset', () => {
    let appels = 0
    const { result } = renderHook(() => useExplore({ ...options, onReset: () => { appels += 1 } }))
    act(() => { result.current.setQuery('pe'); result.current.toggleSort('name') })
    act(() => result.current.reset())
    expect(result.current.query).toBe('')
    expect(result.current.sort).toBeNull()
    expect(result.current.visible).toHaveLength(3)
    expect(appels).toBe(1)
  })
})
