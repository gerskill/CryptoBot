// Test de fumée de l'app iPhone (mobile/) : Playwright + Chromium, profil iPhone.
//
// Sert mobile/ en local, remplace `vendor/supabase.js` par un FAUX client Supabase
// (aucun réseau) et `config.js` par une URL/clé factices, puis vérifie :
//  - config.js vide            -> message « pas encore reliée à Supabase »
//  - pas de session            -> écran de connexion (et mauvais mot de passe)
//  - session + données fraîches -> les 4 onglets avec les chiffres du contrat
//  - publie_a > 10 min         -> bandeau « aucune nouvelle », chiffres grisés
//  - session expirée (401)     -> retour à l'écran de connexion
//  - aucune requête hors de l'app (pas de CDN), zones tactiles >= 44 px
//
// Usage : node mobile/test/smoke.mjs [--captures DOSSIER]

import { createRequire } from 'node:module';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { snapshots, lignesSupabase } from './fixtures.mjs';

const require = createRequire(import.meta.url);
const { chromium, devices } = require('playwright');

const ICI = path.dirname(fileURLToPath(import.meta.url));
const RACINE = path.resolve(ICI, '..');
const APPAREIL = devices['iPhone 15'];
// Le contrat que l'app lit ; le publieur est vérifié contre le même fichier (tests/test_contrat_app.py).
const CONTRAT = JSON.parse(fs.readFileSync(path.join(RACINE, 'contrat.json'), 'utf8'));
const ENCOCHE_PX = 59; // zone de sécurité du haut d'un iPhone 15 (Chromium la donne à 0)
const iCaptures = process.argv.indexOf('--captures');
const CAPTURES = iCaptures > 0 ? path.resolve(process.argv[iCaptures + 1]) : null;

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.webmanifest': 'application/manifest+json', '.json': 'application/json', '.png': 'image/png',
};

const FAUX_CLIENT = `
window.supabase = { createClient(url, cle, options) {
  const f = window.__faux; const ecouteurs = [];
  const prevenir = (ev, s) => ecouteurs.forEach(cb => cb(ev, s));
  f.declencher = prevenir; // le test simule un événement de supabase-js (ex. SIGNED_OUT)
  return {
    auth: {
      getSession: async () => ({ data: { session: f.session }, error: null }),
      signInWithPassword: async ({ email, password }) => {
        if (password !== f.motDePasse) return { data: { session: null }, error: { code: 'invalid_credentials', message: 'Invalid login credentials', status: 400 } };
        f.session = { user: { email }, access_token: 'faux' }; f.erreur = null; prevenir('SIGNED_IN', f.session);
        return { data: { session: f.session }, error: null };
      },
      signOut: async () => { f.session = null; prevenir('SIGNED_OUT', null); return { error: null }; },
      onAuthStateChange: cb => { ecouteurs.push(cb); return { data: { subscription: { unsubscribe() {} } } }; },
    },
    from(table) {
      f.tables.push(table);
      if (table === 'commandes') {
        f.inserts = f.inserts || []; f.commandes = f.commandes || [];
        const resultat = () => Promise.resolve(f.erreur ? { data: null, error: f.erreur } : { data: f.commandes, error: null });
        const chaine = { order: () => chaine, limit: () => resultat(), then: (a, b) => resultat().then(a, b) };
        return {
          select: () => chaine,
          insert: async ligne => { f.inserts.push(ligne); f.commandes = [{ id: f.inserts.length, statut: 'en attente', cree_a: new Date().toISOString(), detail: null, ...ligne }, ...f.commandes]; return { error: null }; },
        };
      }
      return { select: async colonnes => f.erreur ? { data: null, error: f.erreur } : { data: f.lignes, error: null } };
    },
  };
} };`;

let echecs = 0;
const ok = (cond, msg) => { console.log(`${cond ? 'ok  ' : 'ÉCHEC'} ${msg}`); if (!cond) echecs++; };

function servir() {
  const serveur = http.createServer((req, res) => {
    const chemin = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const fichier = path.join(RACINE, chemin.endsWith('/') ? chemin + 'index.html' : chemin);
    if (!fichier.startsWith(RACINE) || !fs.existsSync(fichier) || fs.statSync(fichier).isDirectory()) {
      res.writeHead(404); return res.end('introuvable');
    }
    res.writeHead(200, { 'Content-Type': TYPES[path.extname(fichier)] || 'application/octet-stream' });
    fs.createReadStream(fichier).pipe(res);
  });
  return new Promise(r => serveur.listen(0, '127.0.0.1', () => r(serveur)));
}

async function ouvrir(browser, base, faux, { vraieConfig = false, configVide = false } = {}) {
  const contexte = await browser.newContext({ ...APPAREIL, serviceWorkers: 'block', locale: 'fr-FR', timezoneId: 'Europe/Paris' });
  const page = await contexte.newPage();
  const horsApp = [];
  page.on('request', r => { if (!r.url().startsWith(base)) horsApp.push(r.url()); });
  const erreursJs = [];
  page.on('pageerror', e => erreursJs.push(String(e)));
  await page.addInitScript(f => { window.__faux = { tables: [], ...f }; }, faux);
  await page.route('**/vendor/supabase.js', r => r.fulfill({ contentType: 'text/javascript', body: FAUX_CLIENT }));
  if (configVide) {
    await page.route('**/config.js', r => r.fulfill({
      contentType: 'text/javascript', body: "window.CRYPTOBOT_CONFIG = { supabaseUrl: '', supabaseAnonKey: '' };",
    }));
  } else if (!vraieConfig) {
    await page.route('**/config.js', r => r.fulfill({
      contentType: 'text/javascript',
      body: "window.CRYPTOBOT_CONFIG = { supabaseUrl: 'https://faux.supabase.co', supabaseAnonKey: 'cle-anon-factice' };",
    }));
  }
  await page.goto(base + '/index.html');
  return { page, contexte, horsApp, erreursJs };
}

async function capture(page, nom) {
  if (!CAPTURES) return;
  fs.mkdirSync(CAPTURES, { recursive: true });
  await page.screenshot({ path: path.join(CAPTURES, `${nom}.png`) });
}

// Simule un doigt qui tire l'écran vers le bas de `dy` px (sans relâcher si `relacher` est faux).
async function tirer(page, dy, relacher) {
  await page.evaluate(({ dy, relacher }) => {
    const t = y => new Touch({ identifier: 1, target: document.body, clientX: 150, clientY: y });
    const ev = (type, y) => document.body.dispatchEvent(new TouchEvent(type, {
      touches: type === 'touchend' ? [] : [t(y)], changedTouches: [t(y)], bubbles: true, cancelable: true }));
    if (dy !== null) { ev('touchstart', 100); ev('touchmove', 100 + dy / 2); ev('touchmove', 100 + dy); }
    if (relacher) ev('touchend', 100 + (dy || 0));
  }, { dy, relacher });
}
const boite = (page, sel) => page.locator(sel).evaluate(e => { const r = e.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, height: r.height }; });

const texte = async (page, sel) => (await page.locator(sel).innerText()).replace(/\s+/g, ' ').trim();

async function main() {
  // 0. Les données de test ont exactement la forme de mobile/contrat.json
  const s = snapshots(Date.now());
  const memes = (o, champs) => JSON.stringify(Object.keys(o).sort()) === JSON.stringify([...champs].sort());
  const cles = chemin => (chemin.startsWith('perf.') ? ['perf_7d', 'perf_30d', 'perf_all'] : [chemin.split('.')[0]]);
  ok(memes(s, Object.keys(CONTRAT.cles)), 'fixture : les clés du contrat');
  for (const [cle, champs] of Object.entries(CONTRAT.cles)) ok(memes(s[cle], champs), `fixture ${cle} : champs du contrat`);
  for (const [chemin, champs] of Object.entries(CONTRAT.objets)) {
    ok(cles(chemin).every(c => memes(s[c][chemin.split('.')[1]], champs)), `fixture ${chemin} : champs du contrat`);
  }
  for (const [chemin, champs] of Object.entries(CONTRAT.lignes)) {
    const champ = chemin.split('.')[1];
    ok(cles(chemin).every(c => s[c][champ].length && s[c][champ].every(l => memes(l, champs))), `fixture ${chemin} : lignes du contrat`);
  }

  const serveur = await servir();
  const base = `http://127.0.0.1:${serveur.address().port}`;
  const browser = await chromium.launch();
  try {
    // 1. config.js vide (le modèle avant liaison ; le vrai fichier est désormais rempli)
    {
      const { page, contexte, horsApp, erreursJs } = await ouvrir(browser, base, { session: null, lignes: [] }, { configVide: true });
      await page.waitForSelector('#ecran-config:not([hidden])', { timeout: 5000 });
      ok((await texte(page, '#ecran-config')).includes("L'app n'est pas encore reliée à Supabase : voir docs/APP_IPHONE.md."), 'config vide : message clair');
      ok(await page.locator('#ecran-connexion').isHidden(), 'config vide : pas d\'écran de connexion');
      ok(horsApp.length === 0 && erreursJs.length === 0, `config vide : aucune requête externe ni erreur JS (${[...horsApp, ...erreursJs].join(', ')})`);
      await capture(page, '6-config-vide');
      await contexte.close();
    }

    // 2. pas de session -> connexion ; mauvais puis bon mot de passe
    {
      const lignes = lignesSupabase(snapshots(Date.now()));
      const { page, contexte, erreursJs } = await ouvrir(browser, base, { session: null, lignes, motDePasse: 'bon-mot-de-passe' });
      await page.waitForSelector('#ecran-connexion:not([hidden])', { timeout: 5000 });
      ok(await page.locator('#app').isHidden(), 'sans session : les onglets sont cachés');
      ok(await page.getByRole('button', { name: 'Se connecter' }).isVisible(), 'sans session : bouton « Se connecter »');
      await capture(page, '5-connexion');
      await page.fill('#email', 'killian@example.com');
      await page.fill('#mot-de-passe', 'faux');
      await page.click('#bouton-connexion');
      await page.waitForSelector('#message-connexion:not([hidden])');
      ok((await texte(page, '#message-connexion')) === 'E-mail ou mot de passe incorrect.', 'mauvais mot de passe : message');
      await page.fill('#mot-de-passe', 'bon-mot-de-passe');
      await page.click('#bouton-connexion');
      await page.waitForSelector('#app:not([hidden])', { timeout: 5000 });
      ok((await texte(page, '#phrase-etat')).startsWith('Le bot tourne.'), 'après connexion : onglet État affiché');
      ok(erreursJs.length === 0, `connexion : aucune erreur JS (${erreursJs.join(', ')})`);
      await contexte.close();
    }

    // 3. session + données fraîches : les quatre onglets
    {
      const lignes = lignesSupabase(snapshots(Date.now()));
      const { page, contexte, horsApp, erreursJs } = await ouvrir(browser, base, { session: { user: { email: 'k@example.com' } }, lignes });
      await page.waitForSelector('#app:not([hidden])', { timeout: 5000 });
      await page.waitForFunction(() => document.querySelector('#phrase-etat')?.textContent.trim().length > 0);
      ok(await page.evaluate(() => window.__faux.tables.every(t => t === 'snapshots' || t === 'commandes')), 'lit uniquement les tables snapshots et commandes');
      ok(await page.evaluate(() => (window.__faux.inserts || []).length === 0), "aucune écriture sans action de l'utilisateur");
      ok(await page.locator('#bandeau-perime').isHidden(), 'données fraîches : pas de bandeau');
      ok((await texte(page, '#phrase-etat')) === 'Le bot tourne. Dernier cycle il y a 1 min.', `État : phrase (${await texte(page, '#phrase-etat')})`);
      const etat = await texte(page, '#vue-etat');
      ok(etat.includes('Liquid') && etat.includes('il y a 3 min'), 'État : boucle Liquid et âge de son cycle');
      ok(etat.includes('PAPER'), 'État : mode PAPER affiché');
      ok((await page.locator('#liste-alertes > li').count()) === 4, 'État : 4 alertes');
      ok(etat.includes('Panne') && etat.includes('Zombie') && etat.includes("Arrêt d'urgence") && etat.includes('Sans prix'), 'État : types d\'alerte en français');
      ok(etat.includes('runner') && etat.includes('MOODENG : sortie ZOMBIE_NO_PRICE'), 'État : alerte nomme son bras et son texte');
      // innerText applique le text-transform du titre de section : on compare sans casse.
      ok(etat.toLowerCase().includes('santé des données') && etat.includes('analyse on-chain (Helius)'), 'État : section Santé des données');
      ok((await page.locator('#liste-sources > li').count()) === 4, 'Santé : 4 sources');
      ok(etat.includes('Coupé') && etat.includes('Dégradé') && etat.includes('OK') && etat.includes('À juger'), 'Santé : l\'état est écrit en toutes lettres');
      ok(etat.includes('bundlers 100 %') && etat.includes('38 entrées'), 'Santé : champs vides à l\'entrée');
      ok(await page.locator('nav [data-onglet="etat"]').getAttribute('aria-current') === 'page', 'aria-current sur l\'onglet actif');
      await capture(page, '1-etat');

      await page.click('nav [data-onglet="positions"]');
      ok(await page.locator('nav [data-onglet="positions"]').getAttribute('aria-current') === 'page', 'Positions : aria-current');
      ok(await page.locator('nav [data-onglet="etat"]').getAttribute('aria-current') === null, 'État n\'est plus courant');
      const lignesPos = page.locator('#liste-positions > li');
      ok((await lignesPos.count()) === 3, 'Positions : 3 lignes');
      const bonk = (await lignesPos.filter({ hasText: 'BONK' }).innerText()).replace(/\s+/g, ' ');
      ok(bonk.includes('sniper') && bonk.includes('+18,42 %') && bonk.includes('25,00 $') && bonk.includes('47 min') && bonk.includes('TP1 atteint'), `Positions : ligne BONK complète (${bonk})`);
      const wif = page.locator('#liste-positions > li', { hasText: 'WIF' });
      const wifTexte = (await wif.innerText()).replace(/\s+/g, ' ');
      ok(wifTexte.includes('−6,10 %') && wifTexte.includes('scalp'), `Positions : perte signée « − » et bras nommé (${wifTexte})`);
      ok(/moins 6,1/.test(await wif.locator('[aria-label]').first().getAttribute('aria-label')), 'Positions : libellé VoiceOver « moins … »');
      ok((await texte(page, '#vue-positions')).includes('flotte (somme)'), 'Positions : somme dite « flotte (somme) »');
      await capture(page, '2-positions');

      await page.click('nav [data-onglet="bras"]');
      const lignesBras = page.locator('#liste-bras > li');
      ok((await lignesBras.count()) === 5, 'Bras : 5 bras sur 7 j');
      const base7 = (await lignesBras.filter({ hasText: 'baseline' }).innerText()).replace(/\s+/g, ' ');
      ok(base7.includes('référence') && base7.includes('−6,41 $'), `Bras : témoin « référence » et P&L signé (${base7})`);
      const sniper7 = (await lignesBras.filter({ hasText: 'sniper' }).innerText()).replace(/\s+/g, ' ');
      ok(sniper7.includes('23 trades') && sniper7.includes('WR 60,9 %') && sniper7.includes('[40,8–77,8]') && sniper7.includes('PF 1,62') && sniper7.includes('+12,40 $') && sniper7.includes('échantillon trop court'), `Bras : ligne sniper (${sniper7})`);
      ok((await lignesBras.filter({ hasText: 'runner' }).innerText()).includes('0 trade'), 'Bras : bras sans trade lisible');
      const flotte = await texte(page, '#flotte-bras');
      ok(flotte.startsWith('flotte (somme)') && flotte.includes('133 trades'), `Bras : flotte (somme) en dernier (${flotte})`);
      ok(await page.locator('[data-fenetre="7d"]').getAttribute('aria-pressed') === 'true', 'Bras : 7 j sélectionné par défaut');
      await capture(page, '3-bras');
      await page.click('[data-fenetre="30d"]');
      ok((await lignesBras.filter({ hasText: 'sniper' }).innerText()).includes('69 trades'), 'Bras : 30 j affiche la fenêtre 30 j');
      ok(await page.locator('[data-fenetre="30d"]').getAttribute('aria-pressed') === 'true', 'Bras : 30 j pressé');
      await page.click('[data-fenetre="all"]');
      ok((await lignesBras.filter({ hasText: 'sniper' }).innerText()).includes('115 trades'), 'Bras : « tout » affiche perf_all');

      await page.click('nav [data-onglet="liquid"]');
      const lignesLiq = page.locator('#liste-liquid > li');
      ok((await lignesLiq.count()) === 3, 'Liquid : 3 bras');
      const temoin = (await lignesLiq.filter({ hasText: 'pm_temoin' }).innerText()).replace(/\s+/g, ' ');
      ok(temoin.includes('44,10 $') && temoin.includes('2 ouverts') && temoin.includes('9 réglés') && temoin.includes('4 gagnés nets'), `Liquid : ligne pm_temoin (${temoin})`);
      ok((await lignesLiq.filter({ hasText: 'pm_meteo' }).innerText()).includes('désactivé'), 'Liquid : bras inactif signalé');
      ok((await texte(page, '#vue-liquid')).includes('flotte (somme)'), 'Liquid : somme dite « flotte (somme) »');
      await capture(page, '4-liquid');

      // Pilotage (ADR 021) : la seule écriture est une ligne « commandes », après confirmation.
      await page.click('nav [data-onglet="pilotage"]');
      ok(await page.locator('#vue-pilotage').isVisible(), 'Pilotage : onglet affiché');
      ok((await page.locator('#pilotage-liquid > li').count()) === 3, 'Pilotage : 3 bras Liquid avec un bouton');
      page.once('dialog', d => d.dismiss());
      await page.click('#bouton-stop');
      await page.waitForTimeout(200);
      ok(await page.evaluate(() => (window.__faux.inserts || []).length === 0), 'Pilotage : arrêt annulé = rien envoyé');
      page.once('dialog', d => d.accept());
      await page.click('#bouton-stop');
      await page.waitForFunction(() => (window.__faux.inserts || []).length === 1, null, { timeout: 5000 });
      ok(await page.evaluate(() => window.__faux.inserts[0].action === 'stop'), 'Pilotage : arrêt confirmé = une commande « stop »');
      ok((await texte(page, '#liste-commandes')).includes('en attente'), 'Pilotage : la commande apparaît « en attente »');
      page.once('dialog', d => d.accept());
      await page.click('#pilotage-liquid [data-bras="pm_meteo"]');
      await page.waitForFunction(() => window.__faux.inserts.length === 2, null, { timeout: 5000 });
      ok(await page.evaluate(() => JSON.stringify(window.__faux.inserts[1]) === JSON.stringify({ action: 'pm_toggle', payload: { arm: 'pm_meteo', enabled: true } })),
        'Pilotage : activer pm_meteo = pm_toggle enabled true');
      await page.selectOption('#fork-parent', 'pm_meteo');
      await page.selectOption('#fork-variable', 'edge_min');
      await page.fill('#fork-nombre', '0,5');
      await page.fill('#fork-nom', 'pm_meteo_large');
      await page.click('#bouton-fork');
      ok((await texte(page, '#message-pilotage')).includes('hors bornes'), 'Pilotage : fork hors bornes bloqué dans l\'app');
      await page.fill('#fork-nombre', '0,10');
      page.once('dialog', d => d.accept());
      await page.click('#bouton-fork');
      await page.waitForFunction(() => window.__faux.inserts.length === 3, null, { timeout: 5000 });
      ok(await page.evaluate(() => { const i = window.__faux.inserts[2]; return i.action === 'pm_fork' && i.payload.value === 0.1 && i.payload.name === 'pm_meteo_large' && i.payload.parent === 'pm_meteo'; }),
        'Pilotage : fork valide envoyé (virgule lue comme décimale)');
      const petitsPilotage = await page.evaluate(() => [...document.querySelectorAll('#vue-pilotage button, #vue-pilotage select, #vue-pilotage input')]
        .map(b => b.getBoundingClientRect()).filter(r => r.width > 0 && r.height < 44).length);
      ok(petitsPilotage === 0, 'Pilotage : zones tactiles >= 44 px');
      await capture(page, '5-pilotage');

      // zones tactiles
      const petites = await page.evaluate(() => [...document.querySelectorAll('nav button, [data-fenetre], #bouton-actualiser')]
        .filter(b => b.offsetParent !== null || b.closest('nav'))
        .map(b => b.getBoundingClientRect()).filter(r => r.width > 0 && (r.height < 44 || r.width < 44)).length);
      ok(petites === 0, 'zones tactiles >= 44 px (onglets, fenêtres, actualiser)');
      ok(horsApp.length === 0, `aucune requête hors de l'app : pas de CDN (${horsApp.join(', ')})`);
      ok(erreursJs.length === 0, `aucune erreur JS (${erreursJs.join(', ')})`);
      await contexte.close();
    }

    // 4. données périmées (publiées il y a 14 min)
    {
      const lignes = lignesSupabase(snapshots(Date.now(), { ageMin: 14, cycleMin: 15 }));
      const { page, contexte } = await ouvrir(browser, base, { session: { user: { email: 'k@example.com' } }, lignes });
      await page.waitForSelector('#bandeau-perime:not([hidden])', { timeout: 5000 });
      const bandeau = await texte(page, '#bandeau-perime');
      ok(bandeau.includes('Aucune nouvelle depuis 14 min') && bandeau.includes('Mac éteint, publication ou bot arrêté'), `périmé : bandeau (${bandeau})`);
      ok((await texte(page, '#phrase-etat')) === 'Dernier cycle connu il y a 15 min.', `périmé : la phrase donne le dernier état connu (${await texte(page, '#phrase-etat')})`);
      ok(await page.locator('#phrase-etat').getAttribute('data-etat') === 'perime', 'périmé : phrase en ambre');
      ok(await page.evaluate(() => document.body.classList.contains('perime')), 'périmé : chiffres grisés (classe perime)');
      await capture(page, '7-perime-etat');
      await page.click('nav [data-onglet="bras"]');
      ok(await page.locator('#bandeau-perime').isVisible(), 'périmé : bandeau visible sur les autres onglets');
      const couleur = await page.locator('#liste-bras > li', { hasText: 'sniper' }).locator('.montant').evaluate(e => getComputedStyle(e).color);
      ok(couleur === 'rgb(139, 141, 155)', `périmé : P&L en gris secondaire (${couleur})`);
      const mutations = await page.evaluate(async () => {
        let n = 0;
        const obs = new MutationObserver(m => { n += m.length; });
        obs.observe(document.querySelector('#bandeau-perime'), { childList: true, characterData: true, subtree: true });
        document.querySelector('[data-fenetre="30d"]').click(); // nouveau rendu complet
        await new Promise(r => setTimeout(r, 50));
        obs.disconnect();
        return n;
      });
      ok(mutations === 0, `périmé : bandeau (role=status) non réécrit quand son texte ne change pas (${mutations} mutation(s))`);
      await page.click('[data-fenetre="7d"]');
      await capture(page, '8-perime-bras');
      await contexte.close();
    }

    // 5. session expirée : Supabase répond 401 -> écran de connexion, pas d'écran blanc
    {
      const { page, contexte, erreursJs } = await ouvrir(browser, base, {
        session: { user: { email: 'k@example.com' } }, lignes: [],
        erreur: { status: 401, code: 'PGRST301', message: 'JWT expired' },
      });
      await page.waitForSelector('#ecran-connexion:not([hidden])', { timeout: 5000 });
      ok((await texte(page, '#message-connexion')).includes('Session expirée'), 'session expirée : retour à la connexion avec message');
      ok(await page.locator('#app').isHidden(), 'session expirée : onglets cachés');
      ok(erreursJs.length === 0, 'session expirée : aucune erreur JS');
      await contexte.close();
    }

    // 6. connecté mais rien publié
    {
      const { page, contexte } = await ouvrir(browser, base, { session: { user: { email: 'k@example.com' } }, lignes: [] });
      await page.waitForSelector('#app:not([hidden])', { timeout: 5000 });
      await page.waitForFunction(() => document.querySelector('#phrase-etat')?.textContent.trim().length > 0);
      ok((await texte(page, '#phrase-etat')) === "Pas encore de données : le Mac n'a rien publié.", 'aucune donnée : phrase claire');
      await page.click('nav [data-onglet="positions"]');
      ok((await texte(page, '#vue-positions')).includes("Pas encore de données : le Mac n'a rien publié."), 'aucune donnée : Positions le dit');
      // jeton de rafraîchissement refusé : supabase-js émet SIGNED_OUT
      await page.evaluate(() => window.__faux.declencher('SIGNED_OUT', null));
      await page.waitForSelector('#ecran-connexion:not([hidden])', { timeout: 5000 });
      ok((await texte(page, '#message-connexion')) === 'Session expirée : reconnecte-toi.', 'SIGNED_OUT (rafraîchissement refusé) : connexion avec message');
      await contexte.close();
    }

    // 7. encoche simulée (zone de sécurité du haut à 59 px, comme sur un iPhone 15)
    for (const perime of [false, true]) {
      const lignes = lignesSupabase(snapshots(Date.now(), perime ? { ageMin: 14, cycleMin: 15 } : {}));
      const { page, contexte } = await ouvrir(browser, base, { session: { user: { email: 'k@example.com' } }, lignes });
      await page.waitForFunction(() => document.querySelector('#phrase-etat')?.textContent.trim().length > 0);
      await page.addStyleTag({ content: `:root { --sait: ${ENCOCHE_PX}px; }` });
      const nom = perime ? 'encoche + bandeau' : 'encoche';
      const haut = perime ? (await boite(page, '#bandeau-perime')).bottom : ENCOCHE_PX;
      if (perime) ok(haut >= ENCOCHE_PX + 20, `${nom} : le bandeau descend sous l'encoche (${haut} px)`);
      const repos = await boite(page, '#indicateur-tirer');
      ok(repos.height === 0, `${nom} : indicateur de tirage invisible au repos (${repos.height} px)`);
      ok(Math.abs(repos.top - (haut + 24)) < 1, `${nom} : pas d'espace en trop en haut (contenu à ${repos.top} px, attendu ${haut + 24})`);
      const avant = await page.evaluate(() => window.__faux.tables.length);
      await tirer(page, 160, false);
      const tire = await boite(page, '#indicateur-tirer');
      ok(tire.height >= 40 && tire.top >= haut, `${nom} : en tirant, l'indicateur apparaît sous l'encoche (${tire.height} px à ${tire.top} px)`);
      ok((await texte(page, '#indicateur-tirer')) === 'Relâcher pour actualiser', `${nom} : « Relâcher pour actualiser »`);
      if (!perime) await capture(page, '9-tirer-encoche');
      await tirer(page, null, true);
      await page.waitForFunction(n => window.__faux.tables.length > n, avant, { timeout: 5000 });
      ok((await boite(page, '#indicateur-tirer')).height === 0, `${nom} : relâché, l'indicateur se referme et l'app relit Supabase`);
      await contexte.close();
    }
  } finally {
    await browser.close();
    serveur.close();
  }
  console.log(echecs ? `\n${echecs} échec(s)` : '\nTout est vert.');
  process.exit(echecs ? 1 : 0);
}

main().catch(e => { console.error(e); process.exit(1); });
