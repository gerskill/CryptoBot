import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { BotControls } from './BotControls'
import { usePilotageStore } from '../../lib/pilotageStore'

/** Texte de la description ARIA (aria-describedby), sans jest-dom. */
const description = (el: HTMLElement) => document.getElementById(el.getAttribute('aria-describedby') ?? '')?.textContent ?? ''

function reponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

beforeEach(() => {
  usePilotageStore.setState({ journal: [] })
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (_url, init) => {
    if ((init as RequestInit | undefined)?.method === 'POST') return reponse(200, { ok: true, note: 'transmis à la boucle' })
    return reponse(200, { en_attente: 0, expire_apres_s: 900, actions: ['resume', 'stop'] })
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

const posts = () => (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.filter((c) => (c[1] as RequestInit | undefined)?.method === 'POST')

describe('BotControls', () => {
  it('un clic sur Arrêt d’urgence ouvre la confirmation et n’envoie rien', async () => {
    render(<BotControls />)
    fireEvent.click(screen.getByRole('button', { name: /Arrêt d’urgence/ }))
    const d = await screen.findByRole('alertdialog')
    expect(description(d)).toMatch(/ferme toutes les positions papier/i)
    expect(posts()).toHaveLength(0)
  })
  it('Annuler ne déclenche rien', async () => {
    render(<BotControls />)
    fireEvent.click(screen.getByRole('button', { name: /Arrêt d’urgence/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Annuler' }))
    expect(screen.queryByRole('alertdialog')).toBeNull()
    expect(posts()).toHaveLength(0)
  })
  it('confirmer envoie stop, affiche le résultat en role=status et journalise', async () => {
    render(<BotControls />)
    fireEvent.click(screen.getByRole('button', { name: /Arrêt d’urgence/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Fermer toutes les positions/ }))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/transmis à la boucle/))
    expect(posts()).toHaveLength(1)
    expect(JSON.parse((posts()[0][1] as RequestInit).body as string)).toEqual({ action: 'stop' })
    expect(usePilotageStore.getState().journal[0]).toMatchObject({ kind: 'bot', ok: true })
  })
  it('un refus s’affiche en role=alert avec le quoi/pourquoi/que faire', async () => {
    ;(fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (_u: string, init?: RequestInit) =>
      init?.method === 'POST' ? reponse(401, { detail: 'jeton invalide ou absent' }) : reponse(200, { en_attente: 0, expire_apres_s: 900 }))
    render(<BotControls />)
    fireEvent.click(screen.getByRole('button', { name: /Arrêt d’urgence/ }))
    fireEvent.click(await screen.findByRole('button', { name: /Fermer toutes les positions/ }))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/DASHBOARD_TOKEN/))
    expect(usePilotageStore.getState().journal[0]).toMatchObject({ ok: false })
  })
  it('Reprise ne demande pas de confirmation lourde mais passe par le même chemin', async () => {
    render(<BotControls />)
    fireEvent.click(screen.getByRole('button', { name: /Reprendre les entrées/ }))
    await waitFor(() => expect(posts()).toHaveLength(1))
    expect(JSON.parse((posts()[0][1] as RequestInit).body as string)).toEqual({ action: 'resume' })
  })
  it('annonce la règle des 15 minutes', () => {
    render(<BotControls />)
    expect(screen.getByText(/15 min/)).toBeTruthy()
  })
  it('le champ jeton est un champ mot de passe, jamais enregistré', () => {
    render(<BotControls />)
    const champ = screen.getByLabelText(/Jeton/i)
    expect(champ.getAttribute('type')).toBe('password')
    fireEvent.change(champ, { target: { value: 'TOP-SECRET' } })
    expect(JSON.stringify(localStorage)).not.toContain('TOP-SECRET')
  })
})
