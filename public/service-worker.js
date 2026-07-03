const CACHE_NAME = 'khadamati-shell-v25';
const SHELL = [
  './',
  './index.html',
  './app-icon-192.png',
  './app-icon-512.png',
  './assets/onboarding/khadamati-onboarding.webp',
  './vendor/leaflet.css',
  './vendor/leaflet.js'
];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(key => key !== CACHE_NAME).map(key => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request)
      .then(response => {
        const copy = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)).catch(() => {});
        return response;
      })
      .catch(() => caches.match(event.request).then(cached => cached || caches.match('./index.html')))
  );
});

self.addEventListener('notificationclick', event => {
  event.notification.close();
  const route = event.notification.data?.route || './';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clients => {
      const open = clients.find(client => 'focus' in client);
      if (open) {
        open.postMessage({ type: 'KHADAMATI_NOTIFICATION', route });
        return open.focus();
      }
      return self.clients.openWindow(route);
    })
  );
});

self.addEventListener('push', event => {
  let payload = {};
  try { payload = event.data?.json() || {}; } catch (_) { payload = { body: event.data?.text() || '' }; }
  event.waitUntil(
    self.registration.showNotification(payload.title || 'خدماتي', {
      body: payload.body || payload.message || '',
      icon: './app-icon-192.png',
      badge: './app-icon-192.png',
      tag: payload.tag || payload.id || 'khadamati',
      data: { route: payload.route || './', notificationId: payload.id || '' }
    })
  );
});
