// Service Worker — Marathon Dashboard
// Caches the app shell so it loads instantly and works offline.
const CACHE = 'marathon-v1';
const SHELL = [
  '/marathon_dashboard.html',
  '/manifest.json',
  '/icon.svg',
  'https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js',
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  // Network-first for the HTML (so fresh Garmin data always loads),
  // cache-first for everything else (Chart.js CDN, icons).
  const isHTML = e.request.url.includes('marathon_dashboard.html') || e.request.mode === 'navigate';
  if (isHTML) {
    e.respondWith(
      fetch(e.request)
        .then(res => {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
          return res;
        })
        .catch(() => caches.match(e.request))
    );
  } else {
    e.respondWith(
      caches.match(e.request).then(cached => cached || fetch(e.request))
    );
  }
});
