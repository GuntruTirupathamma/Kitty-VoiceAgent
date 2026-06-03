// Kitty Service Worker v2 — PWA offline + FCM ready
const CACHE = 'kitty-v2';
const STATIC = ['/', '/manifest.json'];

// ── Install: cache shell ─────────────────────────
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(STATIC)));
  self.skipWaiting();
});

// ── Activate: clear old caches ───────────────────
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// ── Fetch strategy ───────────────────────────────
self.addEventListener('fetch', e => {
  const url = e.request.url;

  // API + dynamic calls — network only, never cache
  if (url.includes('/speak') || url.includes('/ai') ||
      url.includes('/sleep-story') || url.includes('/journal') ||
      url.includes('/health')) {
    e.respondWith(fetch(e.request));
    return;
  }

  // Static assets — network first, fall back to cache
  e.respondWith(
    fetch(e.request)
      .then(res => {
        if (res.ok && e.request.method === 'GET') {
          const clone = res.clone();
          caches.open(CACHE).then(c => c.put(e.request, clone));
        }
        return res;
      })
      .catch(() =>
        caches.match(e.request).then(cached => {
          if (cached) return cached;
          // Offline fallback: return cached shell for navigation requests
          if (e.request.mode === 'navigate') return caches.match('/');
        })
      )
  );
});

// ── Firebase Cloud Messaging (background push) ───
// SETUP: go to console.firebase.google.com → Project settings → General → Your apps
// Add a Web app, copy the firebaseConfig, paste below, then uncomment everything.
//
// importScripts('https://www.gstatic.com/firebasejs/10.12.0/firebase-app-compat.js');
// importScripts('https://www.gstatic.com/firebasejs/10.12.0/firebase-messaging-compat.js');
//
// firebase.initializeApp({
//   apiKey:            "PASTE_YOUR_API_KEY",
//   authDomain:        "YOUR_PROJECT.firebaseapp.com",
//   projectId:         "YOUR_PROJECT_ID",
//   storageBucket:     "YOUR_PROJECT.appspot.com",
//   messagingSenderId: "YOUR_SENDER_ID",
//   appId:             "YOUR_APP_ID"
// });
//
// const messaging = firebase.messaging();
//
// messaging.onBackgroundMessage(payload => {
//   self.registration.showNotification(
//     payload.notification?.title || 'Kitty 🐱',
//     {
//       body:  payload.notification?.body  || 'Tap to chat',
//       icon:  '/icon-192.png',
//       badge: '/icon-192.png',
//       data:  { url: '/' }
//     }
//   );
// });
//
// self.addEventListener('notificationclick', e => {
//   e.notification.close();
//   e.waitUntil(clients.openWindow(e.notification.data?.url || '/'));
// });
