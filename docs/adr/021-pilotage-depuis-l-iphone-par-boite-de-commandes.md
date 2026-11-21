# 021. Piloter le bot depuis l'iPhone : une table de commandes validée par le Mac

Date : 2026-11-21
Statut : accepté (demande de Killian : « tu peux tout ajouter, pas juste le mode lecture »)

## Contexte

L'app iPhone (`mobile/`, GitHub Pages + Supabase) était en lecture seule.
Killian veut pouvoir agir depuis le téléphone comme depuis le site et Telegram.
Le téléphone ne voit pas le Mac : il passe par Supabase, que le Mac interroge
déjà toutes les 2 min (`scripts/publie_supabase.py`). Exposer le bot sur le
réseau est une décision de sécurité (skill `securite-exploitation`) : elle est
prise ici, explicitement, avec un périmètre fermé.

## Décision

**Le téléphone dépose, le Mac décide.** L'app n'écrit qu'une chose : une ligne
« en attente » dans la table `commandes` (RLS : chacun ses lignes, aucun droit
anonyme, aucune suppression ; une ligne ne peut pas naître « appliquée »). Le
Mac relit les demandes en attente avant chaque publication, les valide et écrit
« appliquée » ou « refusée » avec la raison, que l'app affiche.

**Quatre actions, rien de plus** (contrainte SQL + liste blanche côté Mac) :
- `stop` / `resume` : déposées dans `data/commandes_bot/` (un fichier par
  commande, écriture atomique), lues par la boucle au cycle suivant et traitées
  EXACTEMENT comme /STOP et /RESUME sur Telegram (`_handle_commands`). Une
  commande lue est supprimée : elle ne s'applique qu'une fois.
- `pm_toggle` / `pm_fork` : passées à `api.server.deposer_demande_prediction`,
  la fonction de la route du site (ADR 017). Mêmes bornes, mêmes refus.

**Une commande de plus de 15 min est refusée** (« expirée ») : un arrêt
d'urgence ne doit pas partir des heures plus tard au réveil du Mac.

## Ce que ça ne permet pas

Aucun ordre réel (le module d'exécution n'existe pas), aucune modification des
réglages d'un bras memecoin (c'est la boucle d'apprentissage qui les possède),
aucun relâchement de `rugcheck` / `authority`, aucune suppression de données.

## Conséquences

- Délai : 2 min (publication) + un cycle (90 s) au pire pour un arrêt
  d'urgence. Telegram reste le chemin le plus rapide.
- Un téléphone volé et déverrouillé peut arrêter le bot papier ou créer un bras
  papier ; il ne peut ni trader ni lire les réglages. Parade : se déconnecter
  de Supabase (Authentication → Users → l'utilisateur → Sign out) et changer le
  mot de passe.
- Le mot de passe et la clé secrète Supabase ne sortent jamais du `.env` du Mac.
