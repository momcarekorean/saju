/* 맘케어 무료 도구 서비스워커
   목적: 홈 화면에 추가했을 때 '앱처럼' 열리게 하는 것(설치 조건)과, 인터넷이 끊겼을 때 마지막으로 받은 화면을 한 번 더 보여 주는 것.
   방식: 화면(HTML) 요청만 '인터넷 먼저'로 받고, 성공하면 사본을 남겨 둔다. 그 밖의 요청(자료 json·이미지·글꼴)은 건드리지 않는다
   → 새 버전을 배포하면 바로 새 화면이 뜨고, 병원·약국 자료 같은 매일 바뀌는 json 이 옛 사본으로 남는 일이 없다. */
const CACHE = "momcare-shell-v1";

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then((c) => c.add("./")).catch(() => {}));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((ks) => Promise.all(ks.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || req.mode !== "navigate") return;                  // 화면 이동만 맡는다
  if (new URL(req.url).origin !== self.location.origin) return;
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok) { const copy = res.clone(); caches.open(CACHE).then((c) => c.put("./", copy)); }
        return res;
      })
      .catch(() => caches.match("./").then((r) => r || new Response("인터넷에 연결되면 다시 열어 주세요.", { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } })))
  );
});
