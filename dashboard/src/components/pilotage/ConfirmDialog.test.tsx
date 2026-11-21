import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { ConfirmDialog } from './ConfirmDialog'

afterEach(cleanup)

/** Texte de la description ARIA (aria-describedby), sans jest-dom. */
const description = (el: HTMLElement) => document.getElementById(el.getAttribute('aria-describedby') ?? '')?.textContent ?? ''

function monter(over: Partial<Parameters<typeof ConfirmDialog>[0]> = {}) {
  const onConfirm = vi.fn()
  const onCancel = vi.fn()
  render(
    <ConfirmDialog title="Arrêt d’urgence" consequence="Ferme toutes les positions papier."
      confirmLabel="Fermer toutes les positions" busy={false} onConfirm={onConfirm} onCancel={onCancel} {...over} />,
  )
  return { onConfirm, onCancel }
}

describe('ConfirmDialog', () => {
  it('est un alertdialog nommé et décrit par sa conséquence', () => {
    monter()
    const d = screen.getByRole('alertdialog', { name: 'Arrêt d’urgence' })
    expect(d.getAttribute('aria-modal')).toBe('true')
    expect(description(d)).toMatch(/Ferme toutes les positions papier/)
  })
  it('met le focus sur Annuler par défaut', () => {
    monter()
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Annuler' }))
  })
  it('Entrée sur le bouton par défaut annule, ne confirme pas', () => {
    const { onConfirm, onCancel } = monter()
    fireEvent.click(document.activeElement as HTMLElement)
    expect(onCancel).toHaveBeenCalledTimes(1)
    expect(onConfirm).not.toHaveBeenCalled()
  })
  it('Échap annule', () => {
    const { onCancel } = monter()
    fireEvent.keyDown(screen.getByRole('alertdialog'), { key: 'Escape' })
    expect(onCancel).toHaveBeenCalled()
  })
  it('confirme seulement au clic sur le bouton de confirmation', () => {
    const { onConfirm } = monter()
    fireEvent.click(screen.getByRole('button', { name: 'Fermer toutes les positions' }))
    expect(onConfirm).toHaveBeenCalledTimes(1)
  })
  it('piège le focus : Tab depuis le dernier bouton revient au premier', () => {
    monter()
    const confirmer = screen.getByRole('button', { name: 'Fermer toutes les positions' })
    confirmer.focus()
    fireEvent.keyDown(confirmer, { key: 'Tab' })
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Annuler' }))
  })
  it('occupé : les deux boutons sont verrouillés et Échap ne ferme pas', () => {
    const { onCancel } = monter({ busy: true })
    expect((screen.getByRole('button', { name: 'Annuler' }) as HTMLButtonElement).disabled).toBe(true)
    expect((screen.getByRole('button', { name: /Envoi en cours/ }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.keyDown(screen.getByRole('alertdialog'), { key: 'Escape' })
    expect(onCancel).not.toHaveBeenCalled()
  })
})
