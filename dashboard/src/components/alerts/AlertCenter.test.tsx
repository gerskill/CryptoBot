import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AlertCenter } from './AlertCenter'
import { AlertBanner } from './AlertBanner'
import { useAlertStore } from '../../lib/alertStore'
import { DEFAULT_SETTINGS } from '../../lib/alertSettings'

const requestPermission = vi.fn(async () => 'granted' as const)

beforeEach(() => {
  localStorage.clear()
  requestPermission.mockClear()
  vi.stubGlobal('Notification', Object.assign(vi.fn(), { permission: 'default', requestPermission }))
  useAlertStore.setState({ settings: DEFAULT_SETTINGS, permission: 'default', alerts: [] })
})
afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

const alert = (key: string, read = false) => ({
  key,
  kind: 'bot-stopped' as const,
  title: `Titre ${key}`,
  detail: 'detail',
  id: key,
  at: 1_800_000_000_000,
  read,
})

describe('AlertCenter', () => {
  it('ne demande JAMAIS la permission au chargement', () => {
    render(<AlertCenter />)
    expect(requestPermission).not.toHaveBeenCalled()
  })

  it('le bouton explicite active les alertes et demande la permission', async () => {
    render(<AlertCenter />)
    fireEvent.click(screen.getByRole('button', { name: /Alertes/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Activer les notifications' }))
    await waitFor(() => expect(requestPermission).toHaveBeenCalledTimes(1))
    expect(useAlertStore.getState().settings.enabled).toBe(true)
    await waitFor(() => expect(screen.getByRole('status').textContent).toContain('autorisées'))
  })

  it('affiche les alertes et permet de tout marquer lu', () => {
    useAlertStore.setState({ alerts: [alert('a'), alert('b')] })
    render(<AlertCenter />)
    fireEvent.click(screen.getByRole('button', { name: /Alertes/ }))
    expect(screen.getByText('Titre a')).toBeTruthy()
    expect(screen.getAllByText('non lu')).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Tout marquer lu' }))
    expect(screen.getAllByText('lu')).toHaveLength(2)
  })

  it('mémorise le seuil de perte à la validation', () => {
    render(<AlertCenter />)
    fireEvent.click(screen.getByRole('button', { name: /Alertes/ }))
    const input = screen.getByLabelText(/Perte d'un bras/)
    fireEvent.change(input, { target: { value: '12' } })
    fireEvent.blur(input)
    expect(useAlertStore.getState().settings.lossPct).toBe(12)
  })

  it('borne le seuil saisi (0 -> 0,5)', () => {
    render(<AlertCenter />)
    fireEvent.click(screen.getByRole('button', { name: /Alertes/ }))
    const input = screen.getByLabelText(/Perte d'un bras/) as HTMLInputElement
    fireEvent.change(input, { target: { value: '0' } })
    fireEvent.blur(input)
    expect(useAlertStore.getState().settings.lossPct).toBe(0.5)
    expect(input.value).toBe('0.5')
  })

  it('le son est désactivé par défaut', () => {
    render(<AlertCenter />)
    fireEvent.click(screen.getByRole('button', { name: /Alertes/ }))
    expect(screen.getByRole('switch').getAttribute('aria-checked')).toBe('false')
  })
})

describe('AlertBanner', () => {
  it('apparaît quand la permission système manque', () => {
    useAlertStore.setState({ settings: { ...DEFAULT_SETTINGS, enabled: true }, alerts: [alert('a')] })
    render(<AlertBanner />)
    expect(screen.getByRole('alert').textContent).toContain('Titre a')
  })
  it('reste caché quand les notifications système sont autorisées ou qu\'il n\'y a rien de non lu', () => {
    useAlertStore.setState({ settings: { ...DEFAULT_SETTINGS, enabled: true }, permission: 'granted', alerts: [alert('a')] })
    const { container } = render(<AlertBanner />)
    expect(container.textContent).toBe('')
    cleanup()
    useAlertStore.setState({ permission: 'denied', alerts: [alert('a', true)] })
    expect(render(<AlertBanner />).container.textContent).toBe('')
  })
})
