const CACHE = 'feedback-hub-shell-v7';
const SHELL = [
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/offline'
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match('/offline')));
    return;
  }

  // Always prefer the newest CSS / JS after a Railway deployment. This avoids
  // old PWA cache entries making newly added buttons appear non-functional.
  if (url.pathname === '/static/style.css' || url.pathname === '/static/app.js' || url.pathname === '/service-worker.js') {
    event.respondWith(fetch(request, {cache: 'no-store'}).catch(() => caches.match(request)));
    return;
  }

  if (url.pathname.startsWith('/static/icons/')) {
    event.respondWith(caches.match(request).then((cached) => cached || fetch(request)));
  }
});
