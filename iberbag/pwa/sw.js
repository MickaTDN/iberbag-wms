const VERSION='ddn-public-pwa-2';
const PUBLIC=['/pwa/','/pwa/index.html','/pwa/app.js','/pwa/vault.js','/pwa/app.css','/pwa/icon.svg','/pwa/icon-192.png','/pwa/icon-512.png','/pwa/manifest.webmanifest'];
self.addEventListener('install',event=>event.waitUntil(caches.open(VERSION).then(c=>c.addAll(PUBLIC))));
// No skipWaiting on installation; updates require explicit user action.
self.addEventListener('message',event=>{if(event.data==='activate-update')self.skipWaiting();});
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('ddn-public-pwa-')&&k!==VERSION).map(k=>caches.delete(k)))).then(()=>self.clients.claim())));
self.addEventListener('fetch',event=>{const u=new URL(event.request.url);if(event.request.method!=='GET'||u.origin!==self.location.origin||u.search||!PUBLIC.includes(u.pathname))return;event.respondWith(caches.open(VERSION).then(c=>c.match(event.request)).then(cached=>cached||fetch(event.request)));});
