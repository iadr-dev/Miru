/* Miru service worker — shows Web Push notifications and routes a tap to the right page.
 * The payload is {title, body, url, id}, encrypted to this browser; it carries text only (the
 * body already ends with the non-advice footer). No caching or offline logic lives here. */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { title: "了然 · Miru", body: event.data ? event.data.text() : "" };
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "了然 · Miru", {
      body: data.body || "",
      icon: "/logo.png",
      tag: data.id || undefined,
      data: { url: data.url || "/settings/alerts" },
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || "/", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if ("focus" in client) {
          client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    }),
  );
});
