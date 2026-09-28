/* Application (PWA) de l'espace client — service worker, servi à la racine (/sw.js).
   - hors connexion : page d'attente au lieu d'une erreur du navigateur ;
   - notifications push : affichage et ouverture de la demande concernée.
   Seuls les fichiers statiques (thème, scripts, icônes) sont mis en cache : jamais les pages
   ni les fichiers des clients. */
const CACHE = 'fs-{{ version }}';
const PRECACHE = [
  '{{ url_for("fs.hors_ligne") }}',
  '{{ url_for("static", filename="fs/theme.css") }}?v={{ version }}',
  '{{ url_for("static", filename="fs/app.js") }}?v={{ version }}',
  '{{ url_for("static", filename="pwa/icon-192.png") }}',
  '{{ url_for("static", filename="pwa/badge-96.png") }}',
  '{{ url_for("static", filename="brand/badge.png") }}',
  '{{ url_for("static", filename="fonts/inter-400.woff2") }}',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(noms => Promise.all(noms.filter(n => n.startsWith('fs-') && n !== CACHE).map(n => caches.delete(n))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  if (req.mode === 'navigate') {
    // pages : toujours le réseau (données à jour) ; la page d'attente seulement hors connexion
    e.respondWith(fetch(req).catch(() => caches.match('{{ url_for("fs.hors_ligne") }}')));
  } else if (url.pathname.startsWith('/static/')) {
    e.respondWith(caches.match(req).then(r => r || fetch(req)));
  }
});

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { texte: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.titre || '{{ shop.name }}', {
    body: d.texte || '',
    icon: '{{ url_for("static", filename="pwa/icon-192.png") }}',
    badge: '{{ url_for("static", filename="pwa/badge-96.png") }}',
    tag: d.tag || undefined,
    renotify: !!d.tag,
    data: { url: d.url || '/' },
  }));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const cible = new URL((e.notification.data && e.notification.data.url) || '/', location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(fenetres => {
    for (const f of fenetres) {
      if (f.url.startsWith(location.origin) && 'focus' in f) { f.navigate(cible); return f.focus(); }
    }
    return self.clients.openWindow(cible);
  }));
});

// Le navigateur a renouvelé l'abonnement : on prévient le serveur (l'ancienne adresse sert de preuve)
self.addEventListener('pushsubscriptionchange', e => {
  e.waitUntil((async () => {
    const cle = (e.oldSubscription && e.oldSubscription.options && e.oldSubscription.options.applicationServerKey) || null;
    if (!cle) return;
    const nouveau = e.newSubscription || await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: cle });
    await fetch('{{ url_for("fs.push_renouveler") }}', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ancien: e.oldSubscription.endpoint, nouveau: nouveau.toJSON() }),
    });
  })());
});
