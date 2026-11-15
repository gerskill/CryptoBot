# Faux RUG_PULL « liquidité −100 % » — diagnostic et correctif proposé (NON appliqué)

Date : 2026-10-11. Statut : proposition. Production non modifiée, bot non redémarré.

## Cause
1. Les candidats venus **uniquement de GMGN trending** sont gardés par `_merge` sans `pair_address`
   (ex. Normie : `pair_address=None`, url gmgn.ai).
2. Au monitoring, `_current_market` sans paire → prix **Jupiter**, liquidité `None`.
   Même chose pour une paire DexScreener `pumpfun` (courbe/migrée) : pas de champ `liquidity` → `0`.
3. `_monitor_positions` enregistre `liquidity=liquidity or 0` → la liquidité **inconnue** devient **0**.
4. `liquidity_drop_pct` compare cette lecture au snapshot du **scan** (liquidité GMGN 105 k$)
   → −100 % → `RUG_PULL` évalué en premier → sortie au 1er tick (≈ 1 min), au prix du marché.
5. Défaut secondaire : la fenêtre mélange la liquidité « meilleure paire » du scan avec celle de la paire suivie.

## Mesures (tous les trades_log, 4 007 sorties finales)
- 290 RUG_PULL (7,2 %) ; 229 « faux » (prix de sortie > −20 % vs entrée, médiane +0,5 %), 57 vrais (≤ −50 %), 4 douteux.
- 225 / 229 faux : le monitoring n'a JAMAIS lu de liquidité (`depth_usd = 0` dans microstructure_log) ; 225 ont le motif « −100 % » ; 209 sortis en ≤ 2 min.
  Vrais rugs : 54 / 57 avaient une liquidité mesurée. Le motif « −100 % » ne concerne qu'1 vrai rug.
- Après la sortie, 186 faux ont été re-tradés dans l'heure : prix médian +1,9 % vs sortie, max médian +6,9 %, 2 seulement effondrés.
- Pic : 148 RUG le 10/10 (129 faux) et 79 le 11/10 (72 faux) : coïncide avec GMGN trending qui alimente réellement le scan.
- Impact P&L direct : faible (faux RUG cumulés ≈ +163 $, sortis presque à plat). Le vrai coût :
  - stratégies jamais exécutées (TP/SL/échelles) sur 1 entrée sur 4 de certains bras ;
  - motifs de sortie faussés pour l'apprentissage ;
  - et surtout, **ces positions n'ont AUCUNE surveillance de liquidité réelle**.
  Un contrefactuel exact n'est pas reconstructible (pas de série de prix post-sortie par position).

## Correctif (rugpull_fix.patch)
- `technical.py` : `Snapshot.liquidity` Optional (None = inconnue) et champ `source` (scan/monitor).
  `liquidity_drop_pct(source=, confirm_readings=, price_crash_pct=)` :
  - ignore les lectures None ;
  - ne compare qu'une même source ;
  - exige N lectures consécutives sous le seuil (on garde la moins sévère des N dernières) ;
  - fait exception si le prix chute aussi ≤ −40 % : une lecture suffit.
  Valeurs par défaut rétro-compatibles.
- `main.py` :
  - `_current_market` renvoie `None` (et non 0) si la paire n'a pas de champ liquidité ;
  - `record_raw(liquidity=liquidity, source="monitor")` ;
  - le détecteur utilise `source="monitor"`, `RUG_CONFIRM_READINGS=2`, `RUG_PRICE_CRASH_PCT=-40`.
- `microstructure_agent.py` : dérive/profondeur calculées sur les seules lectures de liquidité connues.
- `tests/test_rugpull_fix.py` : 15 tests :
  - cas Normie reproduit ;
  - témoin de l'ancien comportement ;
  - confirmation ;
  - fast-path prix ;
  - 0 mesuré ≠ None ;
  - sources ;
  - `_current_market` ;
  - microstructure.

Validation : `git apply --check` OK sur l'arbre courant ; suite complète dans une copie isolée sans .env : 924 passed (909 + 15).

## Risques
- Retard maximal d'un tick (≈ 5 s) sur un vrai rug **sans** chute de prix simultanée. Avec chute de prix ≥ 40 %, aucun retard.
- Positions sans `pair_address` (GMGN) : plus de faux rug, mais aussi **aucune** détection de rug par la liquidité.
  Elles restent protégées par le stop-loss sur prix Jupiter, ce qui était déjà le cas réel aujourd'hui.
- Un vrai rug où DexScreener retire le champ liquidité (None) n'est plus vu par la liquidité. Il est rattrapé par le prix (SL).

## Étapes suivantes recommandées (non incluses)
1. Résoudre une `pair_address` à l'entrée pour les candidats GMGN : `get_tokens_data`, paire à liquidité max, en évitant `pumpfun` si une paire migrée existe.
   Re-résoudre la paire si elle perd sa liquidité (migration pump.fun → PumpSwap/Meteora).
2. Option : confirmation par devis de vente Jupiter en échec avant RUG_PULL quand la liquidité est inconnue.
