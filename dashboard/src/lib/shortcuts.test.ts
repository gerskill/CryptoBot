import { describe, expect, it } from 'vitest'
import { isEditableTarget, resolveKey, SHORTCUT_DOCS } from './shortcuts'

const key = (k: string, extra: Partial<Parameters<typeof resolveKey>[0]> = {}) =>
  ({ key: k, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...extra })
const ctx = { space: 'memecoins' as const, pendingG: false }

describe('resolveKey', () => {
  it('ouvre la palette avec Ctrl+K et Cmd+K', () => {
    expect(resolveKey(key('k', { ctrlKey: true }), ctx)).toEqual({ type: 'palette' })
    expect(resolveKey(key('k', { metaKey: true }), ctx)).toEqual({ type: 'palette' })
  })
  it('ouvre la palette avec « / »', () => {
    expect(resolveKey(key('/'), ctx)).toEqual({ type: 'palette' })
  })
  it('ouvre l’aide avec « ? »', () => {
    expect(resolveKey(key('?', { shiftKey: true }), ctx)).toEqual({ type: 'help' })
  })
  it('change d’onglet avec 1-5', () => {
    expect(resolveKey(key('3'), ctx)).toEqual({ type: 'tab', index: 2 })
    expect(resolveKey(key('9'), ctx)).toBeNull()
  })
  it('bascule d’espace avec s', () => {
    expect(resolveKey(key('s'), ctx)).toEqual({ type: 'space-toggle' })
  })
  it('g puis une lettre va à l’onglet, selon l’espace', () => {
    expect(resolveKey(key('g'), ctx)).toEqual({ type: 'pending-g' })
    expect(resolveKey(key('w'), { ...ctx, pendingG: true })).toEqual({ type: 'tab', index: 2 })
    expect(resolveKey(key('m'), { space: 'liquid', pendingG: true })).toEqual({ type: 'tab', index: 2 })
    expect(resolveKey(key('z'), { ...ctx, pendingG: true })).toBeNull()
  })
  it('reconnaît les chiffres AZERTY par la position physique', () => {
    expect(resolveKey(key('&', { code: 'Digit1' }), ctx)).toEqual({ type: 'tab', index: 0 })
    expect(resolveKey(key('é', { code: 'Digit2' }), ctx)).toEqual({ type: 'tab', index: 1 })
    expect(resolveKey(key('1', { code: 'Digit1', shiftKey: true }), ctx)).toEqual({ type: 'tab', index: 0 })
    expect(resolveKey(key('!', { code: 'Digit1', shiftKey: true }), ctx)).toBeNull()
  })
  it('ne masque jamais les raccourcis du navigateur', () => {
    expect(resolveKey(key('1', { ctrlKey: true }), ctx)).toBeNull()
    expect(resolveKey(key('1', { metaKey: true }), ctx)).toBeNull()
    expect(resolveKey(key('s', { ctrlKey: true }), ctx)).toBeNull()
    expect(resolveKey(key('/', { altKey: true }), ctx)).toBeNull()
  })
})

describe('isEditableTarget', () => {
  it('reconnaît les champs de saisie', () => {
    for (const tag of ['input', 'textarea', 'select']) {
      expect(isEditableTarget(document.createElement(tag))).toBe(true)
    }
    const div = document.createElement('div')
    div.setAttribute('contenteditable', 'true')
    expect(isEditableTarget(div)).toBe(true)
  })
  it('reconnaît searchbox et spinbutton', () => {
    for (const role of ['searchbox', 'spinbutton']) {
      const div = document.createElement('div')
      div.setAttribute('role', role)
      expect(isEditableTarget(div)).toBe(true)
    }
  })
  it('laisse passer le reste', () => {
    expect(isEditableTarget(document.createElement('button'))).toBe(false)
    expect(isEditableTarget(null)).toBe(false)
  })
})

describe('SHORTCUT_DOCS', () => {
  it('documente chaque groupe avec description non vide', () => {
    expect(SHORTCUT_DOCS.length).toBeGreaterThan(4)
    for (const doc of SHORTCUT_DOCS) {
      expect(doc.keys.length).toBeGreaterThan(0)
      expect(doc.label.length).toBeGreaterThan(3)
    }
  })
})
