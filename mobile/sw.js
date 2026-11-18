/* CryptoBot — service worker : la COQUILLE de l'app en cache, JAMAIS les données.
 *
 * Seuls les fichiers listés ci-dessous (même origine) passent par le cache.
 * Tout le reste — en particulier les réponses de Supabase, sur une autre
 * origine — va directement au réseau et n'est jamais stocké : un chiffre
 * affiché vient toujours d'une lecture en direct (anti-objectif de la spec :
 * « montrer de vieux chiffres comme s'ils étaient frais »).
 *
 * Réseau d'abord, cache en secours hors ligne : une mise à jour de l'app est
 * prise dès qu'il y a du réseau. Changer CACHE à chaque modification de la liste.
 */
const CACHE = 'cryptobot-coquille-v1';
const COQUILLE = [
  './', './index.html', './style.css', './app.js', './config.js', './manifest.webmanifest',
  './vendor/supabase.js',
  './icons/icon-192.png', './icons/icon-512.png', './icons/icon-maskable-512.png', './icons/apple-touch-icon.png',
];
const CHEMINS = new Set(COQUILLE.map(c => new URL(c, self.location).pathname));

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(COQUILLE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(cles => Promise.all(cles.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin || !CHEMINS.has(url.pathname)) return; // données : réseau seul
  e.respondWith(
    fetch(req).then(res => {
      if (res && res.ok) { const copie = res.clone(); caches.open(CACHE).then(c => c.put(url.pathname, copie)); }
      return res;
    }).catch(() => caches.match(url.pathname).then(hit => hit || caches.match(new URL('./index.html', self.location).pathname)))
  );
});
