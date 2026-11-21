import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Drawer } from './Drawer'
import { useDrawer } from './useDrawer'
import { Fields } from './Fields'

afterEach(cleanup)

function Harness() {
  const drawer = useDrawer<string>()
  return (
    <div>
      <button onClick={drawer.openFrom('alpha')}>ouvrir alpha</button>
      <button>autre</button>
      <Drawer open={drawer.item !== null} onClose={drawer.close} title={`Détail ${drawer.item}`}>
        <button>premier</button>
        <button>dernier</button>
      </Drawer>
    </div>
  )
}

describe('Drawer', () => {
  it('s’ouvre en dialog modal nommé, et le focus y entre', async () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('ouvrir alpha'))
    const dialog = await screen.findByRole('dialog', { name: 'Détail alpha' })
    expect(dialog.getAttribute('aria-modal')).toBe('true')
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it('Échap ferme et rend le focus au déclencheur', async () => {
    render(<Harness />)
    const trigger = screen.getByText('ouvrir alpha')
    trigger.focus()
    fireEvent.click(trigger)
    await screen.findByRole('dialog')
    fireEvent.keyDown(document.activeElement ?? document.body, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(document.activeElement).toBe(trigger)
  })

  it('piège le focus : Tab depuis le dernier revient au premier, Maj+Tab inverse', async () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('ouvrir alpha'))
    const dialog = await screen.findByRole('dialog')
    const focusables = dialog.querySelectorAll<HTMLElement>('button')
    const premier = focusables[0]
    const dernier = focusables[focusables.length - 1]

    dernier.focus()
    fireEvent.keyDown(dernier, { key: 'Tab' })
    expect(document.activeElement).toBe(premier)

    premier.focus()
    fireEvent.keyDown(premier, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(dernier)
  })

  it('le bouton Fermer ferme le tiroir', async () => {
    render(<Harness />)
    fireEvent.click(screen.getByText('ouvrir alpha'))
    fireEvent.click(await screen.findByRole('button', { name: 'Fermer' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})

describe('Fields', () => {
  it('n’affiche rien pour une valeur absente', () => {
    render(<Fields rows={[['Prix', '1,2'], ['Inconnu', null], ['Vide', undefined], ['Texte vide', '']]} />)
    expect(screen.getByText('Prix')).toBeTruthy()
    expect(screen.queryByText('Inconnu')).toBeNull()
    expect(screen.queryByText('Vide')).toBeNull()
    expect(screen.queryByText('Texte vide')).toBeNull()
  })
  it('affiche un zéro réel', () => {
    render(<Fields rows={[['Gagnés', 0]]} />)
    expect(screen.getByText('0')).toBeTruthy()
  })
})
