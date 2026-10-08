/**
 * The browser's half of web push (screen 21; launch stream identity).
 *
 * Permission is asked only when the person presses Enable push, never on
 * load, and never again after a denial (the browser's own setting is the
 * way back, and the screen says so). The service worker is registered only
 * then too, so a person who never enables push runs no worker at all.
 */

export type PushPermission = "unsupported" | "default" | "granted" | "denied";

export function pushSupported(): boolean {
    return (
        typeof window !== "undefined" &&
        "serviceWorker" in navigator &&
        "PushManager" in window &&
        "Notification" in window
    );
}

export function pushPermission(): PushPermission {
    if (!pushSupported()) return "unsupported";
    return Notification.permission as PushPermission;
}

function keyBytes(base64url: string): Uint8Array {
    const padded = (base64url + "=".repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, "+").replace(/_/g, "/");
    const raw = atob(padded);
    return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

/** "Chrome on Android": what the person sees in their device list. */
export function deviceLabel(userAgent: string = typeof navigator !== "undefined" ? navigator.userAgent : ""): string {
    const browser = /Edg\//.test(userAgent)
        ? "Edge"
        : /Firefox\//.test(userAgent)
          ? "Firefox"
          : /Chrome\//.test(userAgent)
            ? "Chrome"
            : /Safari\//.test(userAgent)
              ? "Safari"
              : "A browser";
    const os = /Android/.test(userAgent)
        ? "Android"
        : /iPhone|iPad/.test(userAgent)
          ? "iPhone or iPad"
          : /Mac OS X/.test(userAgent)
            ? "Mac"
            : /Windows/.test(userAgent)
              ? "Windows"
              : /Linux/.test(userAgent)
                ? "Linux"
                : "";
    return os ? `${browser} on ${os}` : browser;
}

export type BrowserSubscription = {
    endpoint: string;
    keys: { p256dh: string; auth: string };
    device_label: string;
};

/** Ask, register and subscribe. Returns the subscription to send to the
 *  server, or the permission state that stopped it. */
export async function enablePush(publicKey: string): Promise<BrowserSubscription | PushPermission> {
    if (!pushSupported()) return "unsupported";
    const permission = await Notification.requestPermission();
    if (permission !== "granted") return permission as PushPermission;
    const registration = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    await navigator.serviceWorker.ready;
    const existing = await registration.pushManager.getSubscription();
    const subscription =
        existing ??
        (await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: keyBytes(publicKey) as BufferSource,
        }));
    const json = subscription.toJSON() as { endpoint?: string; keys?: { p256dh?: string; auth?: string } };
    return {
        endpoint: json.endpoint ?? subscription.endpoint,
        keys: { p256dh: json.keys?.p256dh ?? "", auth: json.keys?.auth ?? "" },
        device_label: deviceLabel(),
    };
}
