import { afterEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_SETTINGS, loadSettings, sanitizeSettings, saveSettings } from './alertSettings'

afterEach(() => {
  vi.restoreAllMocks()
  localStorage.clear()
})

describe('sanitizeSettings', () => {
  it('valeurs par défaut : désactivé, son coupé', () => {
    expect(DEFAULT_SETTINGS).toEqual({ enabled: false, lossPct: 5, sound: false })
    expect(sanitizeSettings(null)).toEqual(DEFAULT_SETTINGS)
    expect(sanitizeSettings('x')).toEqual(DEFAULT_SETTINGS)
  })
  it('borne le seuil et ignore les types inattendus', () => {
    expect(sanitizeSettings({ enabled: true, lossPct: 500, sound: 'oui' })).toEqual({
      enabled: true,
      lossPct: 100,
      sound: false,
    })
    expect(sanitizeSettings({ lossPct: -3 }).lossPct).toBe(0.5)
    expect(sanitizeSettings({ lossPct: 'abc' }).lossPct).toBe(5)
  })
})

describe('loadSettings / saveSettings', () => {
  it('relit ce qui a été enregistré', () => {
    saveSettings({ enabled: true, lossPct: 8, sound: true })
    expect(loadSettings()).toEqual({ enabled: true, lossPct: 8, sound: true })
  })
  it('JSON corrompu : défauts', () => {
    localStorage.setItem('cryptobot.alerts.v1', '{oops')
    expect(loadSettings()).toEqual(DEFAULT_SETTINGS)
  })
  it('navigation privée : lecture et écriture ne lèvent jamais', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('quota')
    })
    expect(loadSettings()).toEqual(DEFAULT_SETTINGS)
    expect(() => saveSettings(DEFAULT_SETTINGS)).not.toThrow()
  })
})
