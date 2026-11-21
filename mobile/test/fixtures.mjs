// Données de test de l'app iPhone, de la forme EXACTE de mobile/contrat.json
// (vérifié par smoke.mjs ; le publieur, lui, est vérifié contre le même fichier
// par tests/test_contrat_app.py).
// Les heures sont relatives à `maintenant` pour que le test ne vieillisse pas.

const iso = (maintenant, minutesAvant) => new Date(maintenant - minutesAvant * 60000).toISOString();

function perf(fenetre, publie_a, facteur) {
  return {
    publie_a,
    fenetre,
    capital_par_bras: 100.0,
    bras: [
      { bras: 'baseline', trades: 41 * facteur, win_rate: 43.9, ic_bas: 29.9, ic_haut: 58.8, profit_factor: 0.81, pnl_usd: -6.4123, max_drawdown_pct: 11.2, verdict: null },
      { bras: 'sniper', trades: 23 * facteur, win_rate: 60.9, ic_bas: 40.8, ic_haut: 77.8, profit_factor: 1.62, pnl_usd: 12.4, max_drawdown_pct: 4.75, verdict: 'échantillon trop court' },
      { bras: 'scalp', trades: 57 * facteur, win_rate: 52.6, ic_bas: 39.9, ic_haut: 65.0, profit_factor: 1.08, pnl_usd: 1.9, max_drawdown_pct: 7.1, verdict: 'indistinguable' },
      { bras: 'runner', trades: 0, win_rate: 0.0, ic_bas: null, ic_haut: null, profit_factor: null, pnl_usd: 0.0, max_drawdown_pct: 0.0, verdict: 'échantillon trop court' },
      { bras: 'quality', trades: 12 * facteur, win_rate: 33.3, ic_bas: 13.8, ic_haut: 60.9, profit_factor: 0.44, pnl_usd: -9.85, max_drawdown_pct: 13.6, verdict: 'inférieur' },
    ],
    flotte: { trades: 133 * facteur, win_rate: 49.6, pnl_usd: -1.9623, profit_factor: 0.97 },
  };
}

export function snapshots(maintenant, { ageMin = 1, cycleMin = 1 } = {}) {
  const publie_a = iso(maintenant, ageMin);
  return {
    etat: { publie_a, bot: { dernier_cycle: iso(maintenant, cycleMin), mode: 'PAPER', en_pause: false }, liquid: { dernier_cycle: iso(maintenant, cycleMin + 2) } },
    positions: {
      publie_a,
      lignes: [
        { bras: 'sniper', symbole: 'BONK', pnl_pct: 18.42, taille_usd: 25.0, duree_min: 47.5, tp1_atteint: true },
        { bras: 'scalp', symbole: 'WIF', pnl_pct: -6.1, taille_usd: 20.0, duree_min: 12.0, tp1_atteint: false },
        { bras: 'baseline', symbole: 'POPCAT', pnl_pct: 2.3, taille_usd: 20.0, duree_min: 134.2, tp1_atteint: false },
      ],
    },
    perf_7d: perf('7d', publie_a, 1),
    perf_30d: perf('30d', publie_a, 3),
    perf_all: perf('all', publie_a, 5),
    liquid: {
      publie_a,
      bras: [
        { bras: 'pm_temoin', famille: 'transverse', role: 'temoin', actif: true, ouverts: 2, solde_usd: 44.1, regles: 9, gagnes_net: 4 },
        { bras: 'pm_coherence', famille: 'transverse', role: 'regle', actif: true, ouverts: 0, solde_usd: 47.85, regles: 11, gagnes_net: 7 },
        { bras: 'pm_meteo', famille: 'meteo', role: 'parieur', actif: false, ouverts: 0, solde_usd: 46.0, regles: 0, gagnes_net: 0 },
      ],
    },
    donnees: {
      publie_a,
      fenetre_h: 24,
      entrees: 38,
      sources: [
        { nom: 'analyse on-chain (Helius)', etat: 'coupe', detail: '36/38 entrées sans lecture on-chain (erreur 36, absente 0, quota 0)' },
        { nom: 'holders', etat: 'degrade', detail: 'source : gmgn ; morte : birdeye' },
        { nom: 'prix', etat: 'ok', detail: 'source : jupiter' },
        { nom: 'signaux de manipulation (GMGN)', etat: 'inconnu', detail: 'aucune entrée mesurée' },
      ],
      champs_vides: [
        { champ: 'bundlers', vide_pct: 100.0 },
        { champ: 'holders', vide_pct: 61.5 },
        { champ: 'smart money', vide_pct: 0.0 },
      ],
    },
    alertes: {
      publie_a,
      lignes: [
        { quand: iso(maintenant, 35), type: 'PANNE', bras: null, texte: 'liquid_runner en panne depuis 35 min' },
        { quand: iso(maintenant, 130), type: 'SANS_PRIX', bras: 'sniper', texte: 'OGTA : aucun prix depuis 130 min' },
        { quand: iso(maintenant, 190), type: 'ZOMBIE', bras: 'runner', texte: 'MOODENG : sortie ZOMBIE_NO_PRICE' },
        { quand: iso(maintenant, 600), type: 'PANIC_STOP', bras: 'sniper', texte: 'GOAT : sortie PANIC_STOP' },
      ],
    },
  };
}

// Lignes telles que les renvoie `from('snapshots').select('key, value, published_at')`.
export function lignesSupabase(snaps) {
  return Object.entries(snaps).map(([key, value]) => ({ key, value, published_at: value.publie_a }));
}
