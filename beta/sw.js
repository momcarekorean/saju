/* 이 사이트는 서비스워커를 쓰지 않는다. 시험 중 등록됐던 것이 있으면 스스로 지운다. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => { e.waitUntil((async () => {
  try { for (const k of await caches.keys()) await caches.delete(k); await self.registration.unregister();
        for (const c of await self.clients.matchAll({ type: "window" })) c.navigate(c.url); } catch (_) {}
})()); });
