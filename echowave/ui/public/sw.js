/*
 * Decibyl's service worker: web push only (launch stream identity, screen 21).
 *
 * Registered only after a person presses "Enable push" in Settings ->
 * Notifications. It caches nothing and intercepts no requests. A push shows
 * one notification (the server already made the text generic when private
 * previews are on); tapping it opens the link inside Decibyl and nowhere else.
 */

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch {
        data = {};
    }
    const title = typeof data.title === "string" && data.title ? data.title : "Decibyl";
    const url = typeof data.url === "string" && data.url.startsWith("/") && !data.url.startsWith("//") ? data.url : "/";
    event.waitUntil(
        self.registration.showNotification(title, {
            body: typeof data.body === "string" ? data.body : "",
            tag: typeof data.tag === "string" ? data.tag : undefined,
            icon: "/decibyl-mark.png",
            data: { url },
        }),
    );
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const url = (event.notification.data && event.notification.data.url) || "/";
    event.waitUntil(
        self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
            for (const client of windows) {
                if ("focus" in client && new URL(client.url).origin === self.location.origin) {
                    client.navigate(url);
                    return client.focus();
                }
            }
            return self.clients.openWindow(url);
        }),
    );
});
