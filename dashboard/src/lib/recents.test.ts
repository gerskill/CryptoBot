import { beforeEach, describe, expect, it } from 'vitest'
import { loadRecents, MAX_RECENTS, pushRecent, saveRecents } from './recents'

describe('recents', () => {
  beforeEach(() => localStorage.clear())

  it('met le plus récent en tête sans doublon', () => {
    expect(pushRecent(['a', 'b', 'c'], 'b')).toEqual(['b', 'a', 'c'])
  })
  it('borne la longueur', () => {
    const many = Array.from({ length: 10 }, (_, i) => `x${i}`)
    expect(pushRecent(many, 'new')).toHaveLength(MAX_RECENTS)
  })
  it('relit ce qui a été enregistré', () => {
    saveRecents(['a', 'b'])
    expect(loadRecents()).toEqual(['a', 'b'])
  })
  it('ignore un contenu corrompu', () => {
    localStorage.setItem('cryptobot.palette.recents', '{pas du json')
    expect(loadRecents()).toEqual([])
    localStorage.setItem('cryptobot.palette.recents', '[1,"ok",{}]')
    expect(loadRecents()).toEqual(['ok'])
  })
})
