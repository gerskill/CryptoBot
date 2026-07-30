/**
 * Valeurs de repli STABLES pour les sélecteurs zustand.
 *
 * Un sélecteur qui écrit `?? []` fabrique un tableau neuf à chaque rendu :
 * la référence change, zustand croit que l'état a changé, et React part en
 * boucle infinie ("The result of getSnapshot should be cached").
 * Ces constantes gardent la même identité pour toute la durée de vie de la page.
 */
export const EMPTY_ARRAY: never[] = []
export const EMPTY_OBJECT: Record<string, never> = {}
