'use strict';

const CACHE_NAME = 'lhc-transport-static-v19';
const STATIC_ASSETS = [
  '/static/style.css',
  '/static/manifest.webmanifest',
  '/static/icons/transport.svg',
  '/static/icons/transport-192.png',
  '/static/icons/transport-512.png'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys()
      .then(names => Promise.all(
        names.filter(name => name.startsWith('lhc-transport-static-') &&
          name !== CACHE_NAME).map(name => caches.delete(name))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const requestUrl = new URL(event.request.url);
  if (event.request.method !== 'GET' ||
      requestUrl.origin !== self.location.origin ||
      !STATIC_ASSETS.includes(requestUrl.pathname)) {
    return;
  }

  event.respondWith(
    caches.open(CACHE_NAME).then(async cache => {
      const cached = await cache.match(event.request);
      if (cached) return cached;
      const response = await fetch(event.request);
      if (response.ok) await cache.put(event.request, response.clone());
      return response;
    })
  );
});

