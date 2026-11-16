# 017. Modifier les bras de prédiction depuis le site : une boîte de demandes

Date : 2026-11-16
Statut : accepté

## Contexte

Killian veut pouvoir agir depuis le site, pas seulement lire. Or l'API est en
lecture seule par construction : elle ne doit jamais écrire dans un fichier
dont un autre processus est propriétaire, sous peine de casser les écritures
atomiques. Et la méthode du projet interdit de modifier un bras existant :
ses paris déjà pris ne seraient plus comparables.

## Décision

**Une seule route d'écriture**, `POST /api/prediction/requests`, limitée aux
bras PAPIER de prédiction. Elle n'écrit que dans une boîte dont l'API est la
seule rédactrice (`data/prediction/requests.jsonl`, ajout en fin de fichier).

**La simulation applique.** Au début de chaque cycle, `scripts/run_prediction.py`
lit la boîte, valide chaque demande, tient à jour sa surcouche
(`arms_local.json`, écriture atomique) et journalise le sort de la demande
(`requests_log.jsonl`). `config/prediction_arms.json` n'est jamais réécrit.

**Deux actions.**
- `toggle` : activer ou désactiver un bras. Un bras désactivé ne prend plus de
  pari ; ses paris ouverts sont encore réglés.
- `fork` : créer un bras qui reprend son parent et ne change qu'UNE variable,
  dans des bornes fixes (`overlay.py::BOUNDS`, `VARIABLES`). 20 forks au plus.

**Protection.** Jeton `DASHBOARD_TOKEN` s'il est défini ; en-tête
`X-CryptobBot` et corps JSON obligatoires, ce qui impose à un navigateur une
demande de permission que le CORS (GET seulement) refuse à toute page tierce.

## Ce qui n'est pas fait, volontairement

- Aucune écriture vers la boucle memecoin : ses paramètres appartiennent au
  moteur d'apprentissage, et un réglage manuel fausserait la comparaison des
  sept bras. Ce serait une décision à part.
- Aucune modification en place d'un bras, aucune suppression, aucun changement
  de mise, de frais ou de capital fictif depuis le site.
- Aucun ordre : il n'existe toujours aucun code d'exécution.

## Conséquences

- Un changement fait depuis le site vit dans `data/` (non versionné) : il
  survit à un `git pull` mais pas à une suppression de `data/prediction/`.
- Une demande n'est visible qu'au cycle suivant (une à deux minutes).
