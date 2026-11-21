/* CryptoBot — app iPhone : suivi et pilotage, MODE PAPIER.
 *
 * Lit la table Supabase `snapshots` (une ligne par clé : etat, positions,
 * perf_7d, perf_30d, perf_all, liquid, alertes), publiée toutes les 2 min par
 * scripts/publie_supabase.py sur le Mac. Contrat des champs : mobile/contrat.json.
 *
 * Pilotage (ADR 021) : la SEULE écriture est l'ajout d'une ligne dans la table
 * `commandes` (stop, resume, pm_toggle, pm_fork). L'app ne décide rien : le Mac
 * relit la demande, la valide avec les règles du site, l'applique ou la refuse,
 * et écrit le résultat que l'onglet Pilotage affiche. Aucun ordre réel possible.
 *
 * Les données restent en mémoire (jamais de stockage local, jamais en cache du
 * service worker) : un chiffre affiché vient de la dernière lecture, et son âge
 * est toujours dit.
 */
'use strict';

const PERIME_APRES_MS = 10 * 60 * 1000;      // bandeau « aucune nouvelle » (spec)
const BOT_ARRETE_APRES_MS = 180 * 1000;       // comme le dashboard (STALE_AFTER_SECONDS)
const LIQUID_ARRETE_APRES_MS = 300 * 1000;    // comme le dashboard (PREDICTION_STALE_SECONDS)
const RAFRAICHIR_MS = 60 * 1000;
const TIRER_SEUIL_PX = 70;
const PAS_DE_DONNEES = "Pas encore de données : le Mac n'a rien publié.";
const TYPES_ALERTE = { ZOMBIE: 'Zombie', PANIC_STOP: "Arrêt d'urgence", PANNE: 'Panne' };
// Variables qu'un fork peut changer selon le rôle du parent : copie de
// src/prediction/overlay.py (VARIABLES, BOUNDS). Le Mac revalide de toute façon.
const VARIABLES_FORK = {
  temoin: ['fourchette', 'max_jours', 'min_minutes'],
  regle: ['fourchette', 'cote', 'max_jours', 'min_minutes'],
  parieur: ['edge_min', 'estimateur', 'max_jours', 'min_minutes'],
};
const BORNES_FORK = { edge_min: [0.01, 0.30], max_jours: [0.05, 60], min_minutes: [1, 1440] };
const LIBELLES_VARIABLE = {
  fourchette: 'fourchette de prix (0,02 à 0,98)', cote: 'cote (favori / outsider)', estimateur: 'estimateur',
  edge_min: 'avantage minimal (0,01 à 0,30)', max_jours: 'échéance max en jours (0,05 à 60)', min_minutes: 'délai min en minutes (1 à 1440)',
};
const LIBELLES_ACTION = { stop: "Arrêt d'urgence", resume: 'Reprise des entrées', pm_toggle: 'Bras Liquid', pm_fork: 'Nouveau bras Liquid' };

const $ = s => document.querySelector(s);
const etat = { sb: null, donnees: null, commandes: null, lu_a: null, onglet: 'etat', fenetre: '7d', erreur: '', chargement: null, envoi: false };

/* ---------- Formats (français, signe + / − et libellé VoiceOver) ---------- */
const fmt = (v, dec) => new Intl.NumberFormat('fr-FR', { minimumFractionDigits: dec, maximumFractionDigits: dec }).format(v);
const estNombre = v => typeof v === 'number' && Number.isFinite(v);
const pluriel = (n, un, plusieurs) => `${fmt(n, 0)} ${Math.abs(n) >= 2 ? plusieurs : un}`;
const echapper = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/** Montant signé coloré : « +12,40 $ », lu « plus 12,40 dollars ». */
function montant(v, unite) {
  if (!estNombre(v)) return '<span class="montant" aria-label="inconnu">—</span>';
  const signe = v > 0 ? '+' : v < 0 ? '−' : '';
  const mot = v > 0 ? 'plus ' : v < 0 ? 'moins ' : '';
  const classe = v > 0 ? 'gain' : v < 0 ? 'perte' : '';
  const nombre = fmt(Math.abs(v), 2);
  const [symbole, lu] = unite === '%' ? ['%', 'pour cent'] : ['$', 'dollars'];
  return `<span class="montant ${classe}" aria-label="${mot}${nombre} ${lu}">${signe}${nombre} ${symbole}</span>`;
}
const dollars = v => (estNombre(v) ? `${fmt(v, 2)} $` : '—');

function duree(minutes) {
  const m = Math.max(0, Math.floor(minutes));
  if (m < 60) return `${m} min`;
  if (m < 1440) return m % 60 ? `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, '0')}` : `${m / 60} h`;
  return `${Math.floor(m / 1440)} j ${Math.floor((m % 1440) / 60)} h`;
}
function ilYa(iso, maintenant = Date.now()) {
  const ms = maintenant - Date.parse(iso);
  if (!Number.isFinite(ms)) return 'à une heure inconnue';
  if (ms < 60000) return "il y a moins d'une minute";
  return `il y a ${duree(ms / 60000)}`;
}
const heure = iso => new Date(iso).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
const ageMs = iso => Date.now() - Date.parse(iso);

/* ---------- Écrans ---------- */
function montrer(ecran) {
  $('#ecran-config').hidden = ecran !== 'config';
  $('#ecran-connexion').hidden = ecran !== 'connexion';
  $('#app').hidden = ecran !== 'app';
}
function messageConnexion(texte) {
  const el = $('#message-connexion');
  el.textContent = texte || '';
  el.hidden = !texte;
}

/* ---------- Données ---------- */
function publieA() {
  const d = etat.donnees || {};
  const moments = Object.values(d).map(v => Date.parse(v?.publie_a)).filter(Number.isFinite);
  return moments.length ? new Date(Math.max(...moments)).toISOString() : null;
}
const estPerime = () => { const p = publieA(); return p !== null && ageMs(p) > PERIME_APRES_MS; };

function sessionExpiree(erreur) {
  const texte = `${erreur?.code || ''} ${erreur?.message || ''}`;
  return erreur?.status === 401 || erreur?.status === 403 || /PGRST30\d|jwt/i.test(texte);
}

async function charger() {
  if (etat.chargement) return etat.chargement;
  etat.chargement = (async () => {
    try {
      const { data, error } = await etat.sb.from('snapshots').select('key, value, published_at');
      if (error) {
        if (sessionExpiree(error)) {
          try { await etat.sb.auth.signOut({ scope: 'local' }); } catch { /* déjà déconnecté */ }
          etat.donnees = null;
          montrer('connexion');
          messageConnexion('Session expirée : reconnecte-toi.');
          return;
        }
        etat.erreur = 'Lecture impossible (réseau ?). Chiffres de la dernière lecture.';
      } else {
        const donnees = {};
        for (const ligne of data || []) if (ligne && typeof ligne.key === 'string') donnees[ligne.key] = ligne.value;
        etat.donnees = donnees;
        etat.lu_a = Date.now();
        etat.erreur = '';
        await chargerCommandes();
      }
    } catch {
      etat.erreur = 'Lecture impossible (réseau ?). Chiffres de la dernière lecture.';
    }
    afficher();
  })();
  try { await etat.chargement; } finally { etat.chargement = null; }
}

async function chargerCommandes() {
  try {
    const { data, error } = await etat.sb.from('commandes')
      .select('id, action, payload, cree_a, statut, detail, traite_a')
      .order('id', { ascending: false }).limit(10);
    if (!error) etat.commandes = data || [];
  } catch { /* la liste garde la dernière lecture */ }
}

/** Seule écriture de l'app : une demande « en attente », que le Mac validera. */
async function envoyerCommande(action, payload, confirmation) {
  if (etat.envoi) return;
  if (confirmation && !window.confirm(confirmation)) return;
  etat.envoi = true; afficherPilotage();
  try {
    const { error } = await etat.sb.from('commandes').insert({ action, payload: payload || {} });
    if (error) {
      messagePilotage(sessionExpiree(error) ? 'Session expirée : reconnecte-toi.' : "Commande non envoyée (réseau ?). Rien n'a été fait.");
    } else {
      messagePilotage('Commande envoyée. Le Mac la traite dans les 2 min : résultat dans « Dernières commandes ».');
      await chargerCommandes();
    }
  } catch {
    messagePilotage("Commande non envoyée (réseau ?). Rien n'a été fait.");
  } finally { etat.envoi = false; afficherPilotage(); }
}
function messagePilotage(texte) {
  const el = $('#message-pilotage');
  el.textContent = texte || ''; el.hidden = !texte;
}

/* ---------- Rendu ---------- */
function afficher() {
  const perime = estPerime();
  document.body.classList.toggle('perime', perime);
  const p = publieA();
  const bandeau = $('#bandeau-perime');
  bandeau.hidden = !perime;
  // Réécrit seulement si le texte change : role="status" ferait relire le bandeau à VoiceOver à chaque rendu.
  const texteBandeau = perime ? `Aucune nouvelle depuis ${duree(ageMs(p) / 60000)} — Mac éteint, publication ou bot arrêté.` : '';
  if (bandeau.textContent !== texteBandeau) bandeau.textContent = texteBandeau;

  afficherEtat(perime);
  afficherPositions();
  afficherBras();
  afficherLiquid();
  afficherPilotage();

  const cle = { etat: 'etat', positions: 'positions', bras: `perf_${etat.fenetre}`, liquid: 'liquid', pilotage: 'etat' }[etat.onglet];
  const pub = etat.donnees?.[cle]?.publie_a;
  $('#texte-publication').textContent = (pub ? `Publié à ${heure(pub)}, ${ilYa(pub)}.` : 'Rien de publié.')
    + (etat.erreur ? ` ${etat.erreur}` : '');
}

function afficherEtat(perime) {
  const e = etat.donnees?.etat;
  const phrase = $('#phrase-etat');
  let texte, code = 'normal';
  if (!e) texte = PAS_DE_DONNEES;
  else if (perime) {
    // Le bandeau dit déjà « aucune nouvelle depuis X min » : la phrase donne le dernier état connu.
    texte = e.bot?.dernier_cycle ? `Dernier cycle connu ${ilYa(e.bot.dernier_cycle)}.` : 'Aucun cycle connu.';
    code = 'perime';
  }
  else if (!e.bot?.dernier_cycle) { texte = "Le bot n'a encore fait aucun cycle."; code = 'panne'; }
  else if (ageMs(e.bot.dernier_cycle) > BOT_ARRETE_APRES_MS) {
    texte = `Le bot ne tourne plus. Dernier cycle ${ilYa(e.bot.dernier_cycle)}.`; code = 'panne';
  } else texte = `Le bot tourne${e.bot.en_pause ? ', entrées en pause' : ''}. Dernier cycle ${ilYa(e.bot.dernier_cycle)}.`;
  phrase.textContent = texte;
  phrase.dataset.etat = code;

  const liquid = $('#ligne-liquid');
  const cycleLiquid = e?.liquid?.dernier_cycle;
  liquid.hidden = !e;
  liquid.dataset.etat = 'normal';
  if (e && !cycleLiquid) liquid.textContent = 'Boucle Liquid : aucun cycle publié.';
  else if (e && ageMs(cycleLiquid) > LIQUID_ARRETE_APRES_MS && !perime) {
    liquid.textContent = `Boucle Liquid arrêtée : dernier cycle ${ilYa(cycleLiquid)}.`; liquid.dataset.etat = 'panne';
  } else if (e) liquid.textContent = `Boucle Liquid : dernier cycle ${ilYa(cycleLiquid)}.`;
  $('#ligne-mode').textContent = e ? `Mode ${e.bot?.mode || 'PAPER'} : aucune transaction réelle.` : '';

  const alertes = etat.donnees?.alertes?.lignes;
  $('#liste-alertes').innerHTML = !etat.donnees?.alertes
    ? `<li class="vide">${PAS_DE_DONNEES}</li>`
    : !alertes.length
      ? '<li class="vide">Aucune alerte depuis 24 h.</li>'
      : alertes.map(a => `<li>
          <div class="ligne-principale"><span class="nom">${echapper(TYPES_ALERTE[a.type] || a.type)}</span>
          <span class="secondaire">${echapper(ilYa(a.quand))}</span></div>
          <p class="secondaire">${a.bras ? `bras ${echapper(a.bras)}, ` : ''}${echapper(a.texte)}</p></li>`).join('');
}

function afficherPositions() {
  const pos = etat.donnees?.positions;
  const lignes = [...(pos?.lignes || [])].sort((a, b) => String(a.bras).localeCompare(b.bras) || String(a.symbole).localeCompare(b.symbole));
  $('#liste-positions').innerHTML = !pos
    ? `<li class="vide">${PAS_DE_DONNEES}</li>`
    : !lignes.length
      ? '<li class="vide">Aucune position ouverte.</li>'
      : lignes.map(l => `<li>
          <div class="ligne-principale"><span class="nom">${echapper(l.symbole || '?')}</span>${montant(l.pnl_pct, '%')}</div>
          <p class="secondaire">${echapper(l.bras)}, ${dollars(l.taille_usd)}, depuis ${estNombre(l.duree_min) ? duree(l.duree_min) : '?'}${l.tp1_atteint ? ', TP1 atteint' : ''}</p></li>`).join('');
  const total = lignes.reduce((s, l) => s + (estNombre(l.taille_usd) ? l.taille_usd : 0), 0);
  $('#flotte-positions').textContent = lignes.length ? `flotte (somme) : ${pluriel(lignes.length, 'position', 'positions')}, ${dollars(total)} engagés.` : '';
}

function resumeWR(o) {
  if (!estNombre(o.trades) || o.trades === 0 || !estNombre(o.win_rate)) return '';
  const ic = estNombre(o.ic_bas) && estNombre(o.ic_haut);
  const lu = `taux de réussite ${fmt(o.win_rate, 1)} pour cent${ic ? `, intervalle de ${fmt(o.ic_bas, 1)} à ${fmt(o.ic_haut, 1)}` : ''}`;
  return `, <span aria-label="${lu}">WR ${fmt(o.win_rate, 1)} %${ic ? ` [${fmt(o.ic_bas, 1)}–${fmt(o.ic_haut, 1)}]` : ''}</span>`;
}
function resumePF(o) {
  if (!estNombre(o.trades) || o.trades === 0) return '';
  return estNombre(o.profit_factor) ? `, PF ${fmt(o.profit_factor, 2)}` : ', aucune perte';
}

function afficherBras() {
  document.querySelectorAll('[data-fenetre]').forEach(b => b.setAttribute('aria-pressed', String(b.dataset.fenetre === etat.fenetre)));
  const perf = etat.donnees?.[`perf_${etat.fenetre}`];
  $('#capital-bras').textContent = estNombre(perf?.capital_par_bras) ? `Capital de départ : ${dollars(perf.capital_par_bras)} par bras.` : '';
  const bras = perf?.bras || [];
  $('#liste-bras').innerHTML = !perf
    ? `<li class="vide">${PAS_DE_DONNEES}</li>`
    : !bras.length
      ? '<li class="vide">Aucun bras activé.</li>'
      : bras.map(b => `<li>
          <div class="ligne-principale"><span class="nom">${echapper(b.bras)}</span>${montant(b.pnl_usd, '$')}</div>
          <p class="secondaire">${pluriel(b.trades || 0, 'trade', 'trades')}${resumeWR(b)}${resumePF(b)}</p>
          <p class="secondaire">${estNombre(b.max_drawdown_pct) ? `drawdown ${fmt(b.max_drawdown_pct, 1)} %, ` : ''}${b.verdict == null ? 'référence (le témoin)' : `face au témoin : ${echapper(b.verdict)}`}</p></li>`).join('');
  const f = perf?.flotte;
  $('#flotte-bras').innerHTML = f && bras.length
    ? `flotte (somme) : ${pluriel(f.trades || 0, 'trade', 'trades')}${resumeWR(f)}${resumePF(f)}, ${estNombre(f.pnl_usd) ? `<span aria-label="${f.pnl_usd < 0 ? 'moins' : 'plus'} ${fmt(Math.abs(f.pnl_usd), 2)} dollars">${f.pnl_usd > 0 ? '+' : f.pnl_usd < 0 ? '−' : ''}${fmt(Math.abs(f.pnl_usd), 2)} $</span>` : '—'}.`
    : '';
}

function afficherLiquid() {
  const liq = etat.donnees?.liquid;
  const bras = liq?.bras || [];
  $('#liste-liquid').innerHTML = !liq
    ? `<li class="vide">${PAS_DE_DONNEES}</li>`
    : !bras.length
      ? '<li class="vide">Aucun bras Liquid.</li>'
      : bras.map(b => `<li class="${b.actif ? '' : 'inactif'}">
          <div class="ligne-principale"><span class="nom">${echapper(b.bras)}</span><span class="montant" aria-label="solde ${estNombre(b.solde_usd) ? fmt(b.solde_usd, 2) + ' dollars' : 'inconnu'}">${dollars(b.solde_usd)}</span></div>
          <p class="secondaire">${b.famille ? `${echapper(b.famille)}, ` : ''}${pluriel(b.ouverts || 0, 'ouvert', 'ouverts')}, ${pluriel(b.regles || 0, 'réglé', 'réglés')}, ${pluriel(b.gagnes_net || 0, 'gagné net', 'gagnés nets')}${b.actif ? '' : ', désactivé'}</p></li>`).join('');
  const somme = k => bras.reduce((s, b) => s + (estNombre(b[k]) ? b[k] : 0), 0);
  $('#flotte-liquid').textContent = bras.length
    ? `flotte (somme) : solde ${dollars(somme('solde_usd'))}, ${pluriel(somme('ouverts'), 'ouvert', 'ouverts')}, ${pluriel(somme('regles'), 'réglé', 'réglés')}, ${pluriel(somme('gagnes_net'), 'gagné net', 'gagnés nets')}.`
    : '';
}

function texteCommande(c) {
  const p = c.payload || {};
  if (c.action === 'pm_toggle') return `${p.enabled ? 'activer' : 'désactiver'} ${p.arm || '?'}`;
  if (c.action === 'pm_fork') return `${p.name || '?'} (depuis ${p.parent || '?'}, ${p.variable || '?'})`;
  return '';
}

function afficherPilotage() {
  const e = etat.donnees?.etat;
  $('#pilotage-bot').textContent = !e ? PAS_DE_DONNEES
    : e.bot?.en_pause ? 'Entrées en pause (après un arrêt d\'urgence ou 3 pertes).' : 'Entrées actives.';
  $('#bouton-stop').disabled = etat.envoi;
  $('#bouton-reprendre').disabled = etat.envoi;
  $('#bouton-fork').disabled = etat.envoi;

  const bras = etat.donnees?.liquid?.bras || [];
  $('#pilotage-liquid').innerHTML = !bras.length
    ? `<li class="vide">${etat.donnees?.liquid ? 'Aucun bras Liquid.' : PAS_DE_DONNEES}</li>`
    : bras.map(b => `<li><div class="ligne-principale"><span class="nom">${echapper(b.bras)}</span>
        <button type="button" class="bouton-bras" data-bras="${echapper(b.bras)}" data-actif="${b.actif ? '1' : '0'}"${etat.envoi ? ' disabled' : ''}>${b.actif ? 'Désactiver' : 'Activer'}</button></div>
        <p class="secondaire">${b.actif ? 'actif' : 'désactivé'}${b.famille ? `, ${echapper(b.famille)}` : ''}</p></li>`).join('');

  // Formulaire de fork : ne réécrit pas les listes si rien n'a changé (sinon la saisie se perdrait).
  const parent = $('#fork-parent');
  const options = bras.filter(b => VARIABLES_FORK[b.role]).map(b => b.bras).join('|');
  if (parent.dataset.options !== options) {
    const choisi = parent.value;
    parent.innerHTML = bras.filter(b => VARIABLES_FORK[b.role])
      .map(b => `<option value="${echapper(b.bras)}">${echapper(b.bras)}</option>`).join('');
    if (choisi) parent.value = choisi;
    parent.dataset.options = options;
    majVariables();
  }

  const cmds = etat.commandes;
  $('#liste-commandes').innerHTML = !cmds
    ? '<li class="vide">Pas encore lues.</li>'
    : !cmds.length ? '<li class="vide">Aucune commande envoyée.</li>'
      : cmds.map(c => `<li><div class="ligne-principale"><span class="nom">${echapper(LIBELLES_ACTION[c.action] || c.action)}</span>
          <span class="statut-${echapper(c.statut)}">${echapper(c.statut)}</span></div>
          <p class="secondaire">${echapper(texteCommande(c))}${texteCommande(c) ? ', ' : ''}envoyée ${echapper(ilYa(c.cree_a))}${c.detail ? ` — ${echapper(c.detail)}` : ''}</p></li>`).join('');
}

function majVariables() {
  const b = (etat.donnees?.liquid?.bras || []).find(x => x.bras === $('#fork-parent').value);
  const vars = VARIABLES_FORK[b?.role] || [];
  const sel = $('#fork-variable');
  const choisi = sel.value;
  sel.innerHTML = vars.map(v => `<option value="${v}">${echapper(LIBELLES_VARIABLE[v] || v)}</option>`).join('');
  if (vars.includes(choisi)) sel.value = choisi;
  majValeur();
}

function majValeur() {
  const v = $('#fork-variable').value;
  const zone = $('#fork-valeur');
  if (zone.dataset.variable === v) return;
  zone.dataset.variable = v;
  const champ = (id, label, attrs = '') => `<label for="${id}">${label}</label><input id="${id}" type="text" inputmode="decimal" autocomplete="off" ${attrs}>`;
  if (v === 'fourchette') zone.innerHTML = champ('fork-min', 'Prix minimal') + champ('fork-max', 'Prix maximal');
  else if (v === 'cote') zone.innerHTML = '<label for="fork-choix">Nouvelle valeur</label><select id="fork-choix"><option value="favori">favori</option><option value="outsider">outsider</option></select>';
  else if (v === 'estimateur') zone.innerHTML = '<label for="fork-choix">Nouvelle valeur</label><select id="fork-choix"><option value="empirique">empirique</option><option value="lognormal">lognormal</option></select>';
  else if (v) zone.innerHTML = champ('fork-nombre', 'Nouvelle valeur');
  else zone.innerHTML = '';
}

const nombre = id => { const t = ($(id)?.value || '').trim().replace(',', '.'); const n = Number(t); return t && Number.isFinite(n) ? n : null; };

function demandeFork() {
  const parent = $('#fork-parent').value, variable = $('#fork-variable').value;
  const name = $('#fork-nom').value.trim().toLowerCase();
  if (!parent || !variable) return [null, 'Choisis un bras parent et une variable.'];
  if (!/^pm_[a-z0-9_]{3,30}$/.test(name)) return [null, 'Nom invalide : « pm_ » puis 3 à 30 lettres minuscules, chiffres ou « _ ».'];
  let value;
  if (variable === 'fourchette') {
    const min = nombre('#fork-min'), max = nombre('#fork-max');
    if (min === null || max === null || !(0.02 <= min && min < max && max <= 0.98)) return [null, 'Fourchette : 0,02 ≤ min < max ≤ 0,98.'];
    value = { prix_min: min, prix_max: max };
  } else if (variable === 'cote' || variable === 'estimateur') value = $('#fork-choix').value;
  else {
    value = nombre('#fork-nombre');
    const [bas, haut] = BORNES_FORK[variable] || [-Infinity, Infinity];
    if (value === null || value < bas || value > haut) return [null, `Valeur hors bornes (${fmt(bas, 2)} à ${fmt(haut, 2)}).`];
  }
  return [{ parent, name, variable, value }, ''];
}

function brancherPilotage() {
  $('#bouton-stop').addEventListener('click', () => envoyerCommande('stop', {},
    "Arrêt d'urgence : fermer TOUTES les positions papier et mettre les entrées en pause ?"));
  $('#bouton-reprendre').addEventListener('click', () => envoyerCommande('resume', {}, 'Reprendre les entrées du bot ?'));
  $('#pilotage-liquid').addEventListener('click', ev => {
    const b = ev.target.closest('[data-bras]');
    if (!b) return;
    const activer = b.dataset.actif !== '1';
    envoyerCommande('pm_toggle', { arm: b.dataset.bras, enabled: activer }, `${activer ? 'Activer' : 'Désactiver'} le bras ${b.dataset.bras} ?`);
  });
  $('#fork-parent').addEventListener('change', majVariables);
  $('#fork-variable').addEventListener('change', majValeur);
  $('#form-fork').addEventListener('submit', ev => {
    ev.preventDefault();
    const [demande, erreur] = demandeFork();
    if (!demande) return messagePilotage(erreur);
    envoyerCommande('pm_fork', demande, `Créer le bras papier ${demande.name} (copie de ${demande.parent}, ${demande.variable} changée) ?`);
  });
}

/* ---------- Navigation ---------- */
function choisirOnglet(nom) {
  etat.onglet = nom;
  document.querySelectorAll('nav [data-onglet]').forEach(b => {
    if (b.dataset.onglet === nom) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
  });
  document.querySelectorAll('.vue').forEach(v => { v.hidden = v.id !== `vue-${nom}`; });
  window.scrollTo(0, 0);
  afficher();
}

/* Tirer pour rafraîchir : le seul mouvement de l'app. */
function brancherTirer() {
  const ind = $('#indicateur-tirer');
  let depart = null, distance = 0;
  window.addEventListener('touchstart', e => {
    depart = window.scrollY <= 0 && !$('#app').hidden ? e.touches[0].clientY : null; distance = 0;
  }, { passive: true });
  window.addEventListener('touchmove', e => {
    if (depart === null) return;
    distance = Math.max(0, e.touches[0].clientY - depart);
    ind.style.height = `${Math.min(distance / 2, 48)}px`;
    ind.textContent = distance > TIRER_SEUIL_PX ? 'Relâcher pour actualiser' : 'Tirer pour actualiser';
  }, { passive: true });
  window.addEventListener('touchend', () => {
    if (depart !== null && distance > TIRER_SEUIL_PX) charger();
    depart = null; distance = 0; ind.style.height = '0'; ind.textContent = '';
  });
}

function brancherRafraichissement() {
  // Toutes les 60 s au premier plan ; au retour au premier plan, tout de suite.
  setInterval(() => { if (!document.hidden && !$('#app').hidden) charger(); }, RAFRAICHIR_MS);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && !$('#app').hidden) charger(); });
}

/* ---------- Démarrage ---------- */
async function demarrer() {
  const conf = window.CRYPTOBOT_CONFIG || {};
  if (!conf.supabaseUrl || !conf.supabaseAnonKey) return montrer('config');
  if (!window.supabase?.createClient) {
    montrer('connexion');
    return messageConnexion('Bibliothèque Supabase introuvable : recharge la page.');
  }
  const sb = window.supabase.createClient(conf.supabaseUrl, conf.supabaseAnonKey, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: false, storageKey: 'cryptobot-auth' },
  });
  etat.sb = sb;
  sb.auth.onAuthStateChange((evenement) => {
    // jamais d'appel Supabase directement dans ce rappel (recommandation supabase-js)
    // SIGNED_OUT arrive aussi quand le jeton n'a pas pu être rafraîchi : on dit pourquoi.
    if (evenement === 'SIGNED_OUT') setTimeout(() => { etat.donnees = null; montrer('connexion'); messageConnexion('Session expirée : reconnecte-toi.'); }, 0);
  });

  $('#form-connexion').addEventListener('submit', async e => {
    e.preventDefault();
    const email = $('#email').value.trim().toLowerCase(), password = $('#mot-de-passe').value;
    if (!email || !password) return messageConnexion('Entre ton e-mail et ton mot de passe.');
    const bouton = $('#bouton-connexion');
    bouton.disabled = true; messageConnexion('');
    try {
      const { error } = await sb.auth.signInWithPassword({ email, password });
      if (error) {
        const reseau = navigator.onLine === false || error.status === 0 || /fetch|network|load failed/i.test(error.message || '');
        return messageConnexion(reseau ? 'Pas de connexion internet : réessaie quand tu as du réseau.' : 'E-mail ou mot de passe incorrect.');
      }
      $('#mot-de-passe').value = '';
      montrer('app');
      await charger();
    } catch {
      messageConnexion('Pas de connexion internet : réessaie quand tu as du réseau.');
    } finally { bouton.disabled = false; }
  });
  document.querySelectorAll('nav [data-onglet]').forEach(b => b.addEventListener('click', () => choisirOnglet(b.dataset.onglet)));
  document.querySelectorAll('[data-fenetre]').forEach(b => b.addEventListener('click', () => { etat.fenetre = b.dataset.fenetre; afficher(); }));
  $('#bouton-actualiser').addEventListener('click', () => charger());
  brancherPilotage();
  brancherTirer();
  brancherRafraichissement();
  setInterval(() => { if (!$('#app').hidden) afficher(); }, 30000); // les âges avancent même sans réseau

  let session = null;
  try { session = (await sb.auth.getSession()).data?.session || null; } catch { session = null; }
  if (!session) return montrer('connexion');
  montrer('app');
  await charger();
}

if ('serviceWorker' in navigator && location.protocol === 'https:') {
  navigator.serviceWorker.register('sw.js').catch(() => { /* l'app marche sans */ });
}
demarrer();
