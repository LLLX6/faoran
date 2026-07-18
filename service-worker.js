const CACHE_NAME = 'khadamati-app-shell-v50-maps-availability';
const SHELL = [
  './',
  './index.html',
  './app-icon-192.png',
  './app-icon-512.png',
  './assets/onboarding/v49/user-service.webp',
  './assets/onboarding/v49/user-direct-request.webp',
  './assets/onboarding/v49/user-matching.webp',
  './assets/onboarding/v49/user-track.webp',
  './assets/onboarding/v49/guest-browse.webp',
  './assets/onboarding/v49/guest-compare.webp',
  './assets/onboarding/v49/guest-signin.webp',
  './assets/onboarding/v49/guest-privacy.webp',
  './assets/onboarding/v49/provider-profile.webp',
  './assets/onboarding/v49/provider-opportunity.webp',
  './assets/onboarding/v49/provider-availability.webp',
  './assets/onboarding/v49/provider-offer.webp',
  './assets/onboarding/v49/company-profile.webp',
  './assets/onboarding/v49/company-dispatch.webp',
  './assets/onboarding/v49/company-analytics.webp',
  './assets/onboarding/v49/company-team.webp',
  './assets/ads/v45/home-services.webp',
  './assets/ads/v45/nearby-services.webp',
  './assets/ads/v45/business-services.webp',
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

self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);
  const privatePath = /\/(api|media|uploads)\//.test(url.pathname);
  if (url.origin !== self.location.origin || privatePath) {
    event.respondWith(fetch(event.request, { cache: 'no-store' }));
    return;
  }
  const acceptsHtml = event.request.headers.get('accept')?.includes('text/html');
  if (event.request.mode === 'navigate' || acceptsHtml) {
    event.respondWith(
      fetch(event.request, { cache: 'no-store' })
        .then(response => {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put('./index.html', copy)).catch(() => {});
          return response;
        })
        .catch(() => caches.match('./index.html'))
    );
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response.ok && response.type === 'basic') {
          const copy = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)).catch(() => {});
        }
        return response;
      })
      .catch(() => caches.match(event.request))
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
