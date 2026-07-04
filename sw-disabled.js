/* Soundboard service worker — offline support.
   Strategy:
   - soundbites.json  -> network first (so edits show up), cached fallback
   - sounds/* (audio) -> cache first, with Range-request support for iOS Safari
   - everything else  -> cache first, refreshed in the background            */

const CACHE = 'soundboard-v2';
const CORE = ['./', './index.html', './manifest.webmanifest', './icon-180.png', './icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE)
      .then(c => Promise.allSettled(CORE.map(u => c.add(u))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);

  // Manifest of sounds: always try the network so updates appear immediately.
  if (url.pathname.endsWith('soundbites.json')) {
    e.respondWith(
      fetch(e.request)
        .then(r => {
          const copy = r.clone();
          caches.open(CACHE).then(c => c.put('soundbites.json', copy));
          return r;
        })
        .catch(() => caches.match('soundbites.json'))
    );
    return;
  }

  // Audio: cache-first with proper 206 Partial Content responses.
  if (url.pathname.includes('/sounds/') || e.request.destination === 'audio') {
    e.respondWith(audioResponse(e.request));
    return;
  }

  // Shell, fonts, icons: cache first, refresh in background.
  e.respondWith(
    caches.match(e.request).then(hit => {
      const net = fetch(e.request)
        .then(r => {
          if (r.ok || r.type === 'opaque') {
            const copy = r.clone();
            caches.open(CACHE).then(c => c.put(e.request, copy));
          }
          return r;
        })
        .catch(() => hit);
      return hit || net;
    })
  );
});

async function audioResponse(request) {
  const cache = await caches.open(CACHE);
  let full = await cache.match(request.url);
  if (!full) {
    try {
      full = await fetch(request.url);
      if (full.ok) await cache.put(request.url, full.clone());
    } catch (err) {
      return new Response('', { status: 404 });
    }
  }
  const range = request.headers.get('range');
  if (!range) return full.clone();

  // iOS Safari asks for byte ranges; serve a real 206 from the cached bytes.
  const buf = await full.clone().arrayBuffer();
  const m = /bytes=(\d+)-(\d+)?/.exec(range);
  const start = m ? Number(m[1]) : 0;
  const end = m && m[2] ? Math.min(Number(m[2]), buf.byteLength - 1) : buf.byteLength - 1;
  return new Response(buf.slice(start, end + 1), {
    status: 206,
    statusText: 'Partial Content',
    headers: {
      'Content-Type': full.headers.get('Content-Type') || 'audio/mpeg',
      'Content-Range': 'bytes ' + start + '-' + end + '/' + buf.byteLength,
      'Content-Length': String(end - start + 1),
      'Accept-Ranges': 'bytes'
    }
  });
}
